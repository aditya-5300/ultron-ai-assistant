"""Seal separate Phase 5 data, select on development/validation, replay holdout offline."""

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from statistics import median
from time import perf_counter

import numpy as np
import yaml

from ultron_health_ml.features.config import FEATURE_SCHEMA_VERSION
from ultron_health_ml.features.engineering import iter_features
from ultron_health_ml.forecast.baselines import FEATURES, METHODS, UNKNOWN, History, Signal, fit_noise, measurement, scores
from ultron_health_ml.forecast.contract import METRICS, SYSTEMS, Actionable, cpu_context, read_experiment, read_targets
from ultron_health_ml.forecast.datasets import labels, open_lock, output_directory, prepare, references, telemetry
from ultron_health_ml.forecast.evaluation import budget, choose, evaluate, future_label, valuable
from ultron_health_ml.forecast.lifecycle import Lifecycle, Point
from ultron_health_ml.hybrid.config import CRITICAL_LIMITS
from ultron_health_ml.hybrid.detector import critical_condition
from ultron_health_ml.models.artifact import library_versions, write_json
from ultron_health_ml.models.datasets import sha256
from ultron_health_ml.models.training import csv_report

SELECTION = "Choose a simple point using only development and validation. Prefer meeting both budget and usefulness gates, then budget, lower validation burden, useful recall, precision and lead; ties prefer shorter horizons."


def grid(targets):
    return tuple(Point(key, method, horizon, strength) for key in SYSTEMS for method in METHODS
                 if method != "eta" or key == "disk_capacity" for horizon in targets.targets[key].horizon_seconds
                 for strength in (.65, .85))


class Safety:
    """Offline observation of the deterministic lane, before any forecast scoring."""
    def __init__(self):
        self.active = dict.fromkeys(CRITICAL_LIMITS, False)
        self.openings = {key: [] for key in CRITICAL_LIMITS}
        self.checks = self.misses = 0

    def step(self, now, record, values):
        for key, (metric, _, _) in CRITICAL_LIMITS.items():
            reading = record.metrics.get(metric)
            raw = (reading.usable_value() if reading is not None and reading.observation_time is not None
                   and now - reading.observation_time.timestamp() < FEATURES.max_cached_age_seconds else None)
            expected = critical_condition(key, raw)
            actual = critical_condition(key, values[metric])
            self.checks += expected
            self.misses += expected != actual
            if actual and not self.active[key]:
                self.openings[key].append(now)
            if values[metric] is not None:
                self.active[key] = actual


def replay(run, source, targets, noise, points, measure=False):
    by_key = {key: [point for point in points if point.subsystem == key] for key in SYSTEMS}
    machines = {point.name: Lifecycle(run["run"], point, targets.targets[point.subsystem], targets.version)
                for point in points}
    trackers = {key: Actionable(key, targets.targets[key]) for key in SYSTEMS}
    histories = {key: History(key) for key in SYSTEMS}
    usable = {key: [] for key in SYSTEMS}
    detections = {key: [] for key in SYSTEMS}
    stamps, timings = [], []
    raw_digest = hashlib.sha256()
    monitored = 0.
    failed = 0
    safety = Safety()
    rows = iter(iter_features(source.window, FEATURES, dataset_version=source.dataset_version))
    for record in source.window.records:
        started = perf_counter()
        row = next(rows)
        now = row.sample_time.timestamp()
        values = {metric: row.values.get(f"{metric}__current") for metric in METRICS}
        safety.step(now, record, values)
        if stamps:
            monitored += min(now - stamps[-1], 2 * record.nominal_interval_seconds)
        stamps.append(now)
        raw = []
        for key in SYSTEMS:
            target = targets.targets[key]
            observed, fresh = measurement(record, row, target)
            value = values[target.metric]
            if fresh and (key != "cpu" or cpu_context(values) is not None):
                usable[key].append(observed)
            transition = trackers[key].update(now, values, observed, fresh)
            if transition == "actionable":
                detections[key].append(now)
            if fresh:
                histories[key].add(observed, value, target.max_gap_seconds)
            try:
                signals = scores(key, target, values, trackers[key], histories[key], noise[key])
            except Exception:
                failed += 1
                signals = dict.fromkeys(METHODS, UNKNOWN)
            for point in by_key[key]:
                signal = signals.get(point.method, Signal(0., sufficient=value is not None))
                machines[point.name].step(now, observed, fresh, value, signal, transition)
                if measure:
                    raw.append((point.name, signal.score, signal.eta_seconds, signal.sufficient))
        if measure:
            timings.append((perf_counter() - started) * 1000)
            raw_digest.update(json.dumps((now, raw), separators=(",", ":"), allow_nan=False).encode())
    traces = {}
    for point in points:
        machine = machines[point.name]
        key = point.subsystem
        traces[point.name] = dict(run=run["run"], episodes=machine.episodes, interactions=machine.interactions,
                                  detections=detections[key], usable=usable[key], monitored=monitored,
                                  finish=stamps[-1], rows=machine.rows, raw=machine.raw,
                                  raw_starts=machine.raw_starts, suppressed=machine.suppressed,
                                  silent=machine.silent, unknown=machine.unknown,
                                  critical=safety.openings.get(key, []))
    return traces, stamps, dict(checks=safety.checks, mismatches=safety.misses,
                               openings=safety.openings, scorer_failures=failed), raw_digest.hexdigest(), timings


def scored(root, runs, targets, noise, points, *, sources=None, copies=1):
    traces = [{point.name: [] for point in points} for _ in range(copies)]
    raw = [[] for _ in traces]
    safety = [[] for _ in traces]
    timings, stamps = [], {}
    for run in runs:
        source = sources[run["run"]] if sources is not None else telemetry(root, run)
        for index in range(copies):
            output, times, critical, digest, elapsed = replay(run, source, targets, noise, points, measure=copies > 1)
            raw[index].append(digest)
            safety[index].append(critical)
            for name, trace in output.items():
                traces[index][name].append(trace)
            if index == 0:
                stamps[run["run"]] = times
                timings.extend(elapsed)
    truths = {run["run"]: labels(root, run) for run in runs}
    evaluated = [{point.name: evaluate(point, targets.targets[point.subsystem], replica[point.name], truths)
                  for point in points} for replica in traces]
    return evaluated, raw, safety, timings, stamps, truths


def digest_decisions(evaluated, raw, safety, acceptance):
    content = dict(results=evaluated, raw_predictions=raw, safety=safety, acceptance=acceptance)
    return hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def prevalence(point, events, stamps, truths):
    observable = {(item["run"], item["event_id"]): item["observable"] for item in events}
    positive = eligible = 0
    for run, times in stamps.items():
        actual = [event for event in truths[run][0] if event.subsystem == point.subsystem]
        for time in times:
            if time + point.horizon > times[-1]:
                break
            state, event = future_label(time, point.horizon, actual)
            if state == "late" or (event and not observable[run, event.event_id]):
                continue
            eligible += 1
            positive += state == "future"
    return dict(eligible_rows=eligible, future_positive_rows=positive,
                positive_prevalence=positive / eligible if eligible else None)


def gate(selected, results, prequalified, config, safety, parity, p95):
    safety_pass = sum(item["checks"] for item in safety) > 0 and not any(
        item["mismatches"] or item["scorer_failures"] for item in safety)
    output = {}
    for key, point in selected.items():
        result = results[point.name][0]
        checks = dict(safety=safety_pass, preholdout=prequalified[key], nuisance=budget(result, config),
                      usefulness=valuable(result, config), reproducibility=parity, performance=p95 < 100)
        decision = ("FORECASTING CANDIDATE" if all(checks.values()) else
                    "INSUFFICIENT EVIDENCE" if result["observable_events"] < config.min_observable_events
                    else "DETECTION ONLY")
        output[key] = dict(decision=decision, point=point.name, gates=checks)
    candidates = [key for key in SYSTEMS if output[key]["decision"] == "FORECASTING CANDIDATE"]
    overall = ("GO TO PHASE 6 WITH FORECASTING SCOPE: " + ", ".join(candidates) if candidates else
               "REPEAT PHASE 5" if not (safety_pass and parity and p95 < 100) or any(
                   item["decision"] == "INSUFFICIENT EVIDENCE" for item in output.values())
               else "STOP FORECASTING PIPELINE")
    return overall, output


def run(inputs, output, config, targets, lock_hash, prior):
    manifest = open_lock(inputs, config, targets, lock_hash)
    if output.resolve() == inputs.resolve() or inputs.resolve() in output.resolve().parents:
        raise ValueError("report output cannot be inside locked inputs")
    output_directory(output)
    write_json(output / "forecast_target_contract.json", targets.model_dump(mode="json"))
    (output / "phase5_config.yaml").write_text(yaml.safe_dump(dict(settings=config.model_dump(mode="json"),
        features=FEATURES.model_dump(mode="json"), selection=SELECTION), sort_keys=False), encoding="utf8")
    points = grid(targets)
    development_runs = [item for item in manifest["runs"] if item["split"] == "development"]
    train_sources = {item["run"]: telemetry(inputs, item) for item in development_runs}
    noise = fit_noise([(item, train_sources[item["run"]]) for item in development_runs], targets)
    development = scored(inputs, development_runs, targets, noise, points, sources=train_sources)[0][0]
    del train_sources
    validation = scored(inputs, [item for item in manifest["runs"] if item["split"] == "validation"],
                        targets, noise, points)[0][0]
    selected, prequalified, finalists = {}, {}, {}
    for key in SYSTEMS:
        points_for_system = [point for point in points if point.subsystem == key]
        winner = choose(points_for_system, development, validation, config)
        selected[key] = winner
        prequalified[key] = all(budget(stage[winner.name][0], config) and valuable(stage[winner.name][0], config)
                                for stage in (development, validation))
        finalists[key] = [Point(key, "detection_only", 0, 1.), *[
            choose([point for point in points_for_system if point.method == method], development, validation, config)
            for method in METHODS if method != "eta" or key == "disk_capacity"
        ]]
        if winner not in finalists[key]:
            finalists[key].append(winner)
    write_json(output / "horizon_selection.json", dict(selection=SELECTION, prequalified=prequalified,
        selected={key: asdict(point) for key, point in selected.items()},
        finalists={key: [asdict(point) for point in group] for key, group in finalists.items()},
        training_noise_floors=noise, preprocessing_runs=[item["run"] for item in development_runs],
        locked_inputs_sha256=lock_hash))
    selection_hash = sha256(output / "horizon_selection.json")
    holdout = [item for item in manifest["runs"] if item["split"] == "holdout"]
    holdout_points = tuple(point for key in SYSTEMS for point in finalists[key])
    replicas, raw, safety, timings, stamps, truth = scored(inputs, holdout, targets, noise, holdout_points, copies=2)
    p95 = float(np.quantile(timings, .95))
    deterministic = digest_decisions(replicas[0], raw[0], safety[0], {}) == digest_decisions(
        replicas[1], raw[1], safety[1], {})
    acceptance = [gate(selected, results, prequalified, config, checks, deterministic, p95)
                  for results, checks in zip(replicas, safety, strict=True)]
    digests = [digest_decisions(results, counts, checks, decision)
               for results, counts, checks, decision in zip(replicas, raw, safety, acceptance, strict=True)]
    if digests[0] != digests[1] and deterministic:
        raise ValueError("deterministic acceptance decisions diverged")
    decision, subsystem = acceptance[0]
    final = replicas[0]
    write_json(output / "acceptance_gates.json", dict(decision=decision, subsystem=subsystem,
        selected_results={key: final[point.name][0] for key, point in selected.items()},
        performance=dict(samples=len(timings), median_ms=median(timings), p95_ms=p95),
        learned_models="not evaluated; simple baselines first",
        critical_checks=sum(item["checks"] for item in safety[0]),
        critical_mismatches=sum(item["mismatches"] for item in safety[0])))
    summary_rows = []
    for point in holdout_points:
        result, events, *_ = final[point.name]
        row = dict(point=point.name, subsystem=point.subsystem, method=point.method, horizon=point.horizon,
                   selected=selected[point.subsystem] == point, **result)
        if point.horizon:
            row.update(prevalence(point, events, stamps, truth))
        else:
            row.update(eligible_rows=None, future_positive_rows=None, positive_prevalence=None)
        summary_rows.append(row)
    csv_report(output / "baseline_comparison.csv", summary_rows)
    csv_report(output / "subsystem_forecast_evaluation.csv", [row for row in summary_rows if row["selected"]])
    csv_report(output / "development_validation_frontier.csv", [dict(split=split, point=point.name, **stage[point.name][0])
               for split, stage in (("development", development), ("validation", validation)) for point in points])
    for filename, index in (("forecast_event_evaluation.csv", 1), ("forecast_episode_evaluation.csv", 2),
                             ("nuisance_evaluation.csv", 3), ("detection_forecast_interaction.csv", 4)):
        csv_report(output / filename, [dict(point=name, **row) for name, result in final.items() for row in result[index]])
    csv_report(output / "lead_time_evaluation.csv", [dict(point=name, **row) for name, result in final.items()
               for row in result[1] if row["lead_seconds"] is not None])
    csv_report(output / "duplicate_notice_evaluation.csv", [dict(point=row["point"], subsystem=row["subsystem"],
               raw_candidate_rows=row["raw_candidate_rows"], raw_candidate_starts=row["raw_candidate_starts"],
               deduplicated_warning_episodes=row["interruptions"], repeated_confirmations=row["repeated_raw_confirmations"],
               duplicate_forecast_episodes=row["duplicate_forecasts"],
               detection_notifications_saved=row["detection_notifications_saved"]) for row in summary_rows])
    write_json(output / "locked_holdout_manifest.json", dict(definition=manifest["definition"], runs=holdout,
               locked_inputs_sha256=lock_hash, horizon_selection_sha256=selection_hash))
    write_json(output / "reproducibility_manifest.json", dict(reference=prior, input_manifest=manifest,
               horizon_selection_sha256=selection_hash, feature_schema=FEATURE_SCHEMA_VERSION,
               feature_fingerprint=FEATURES.fingerprint(), environment=library_versions(),
               raw_replay_digests=raw, decisions_sha256=digests[0], replicate_sha256=digests[1],
               independent_safety_replays=safety))
    return decision, digests[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("prepare", "evaluate"))
    parser.add_argument("--config", type=Path, default=Path("ml/configs/phase5_config.yaml"))
    parser.add_argument("--targets", type=Path, default=Path("ml/configs/forecast_target_contract.json"))
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--locked-sha256")
    parser.add_argument("--reference-root", type=Path)
    args = parser.parse_args()
    config, targets = read_experiment(args.config), read_targets(args.targets)
    if args.operation == "prepare":
        print(prepare(args.inputs, config, targets))
    else:
        if args.output is None or not args.locked_sha256:
            parser.error("evaluate requires --output and --locked-sha256")
        print(*run(args.inputs, args.output, config, targets, args.locked_sha256,
                   references(args.reference_root, args.config.parent)))


if __name__ == "__main__":
    main()
