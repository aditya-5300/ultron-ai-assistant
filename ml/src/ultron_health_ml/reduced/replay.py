"""Calibrate each method on development and validation, then open one locked holdout."""

import argparse
from dataclasses import asdict
from pathlib import Path
from statistics import median
from time import perf_counter

import numpy as np
import yaml

from ultron_health_ml.features.config import FEATURE_SCHEMA_VERSION, FeatureConfig
from ultron_health_ml.features.engineering import iter_features
from ultron_health_ml.hybrid.config import CRITICAL_LIMITS
from ultron_health_ml.hybrid.datasets import open_events, open_telemetry
from ultron_health_ml.hybrid.detector import critical_condition
from ultron_health_ml.hybrid.evaluation import Trace, wilson
from ultron_health_ml.hybrid.replay import summary_row
from ultron_health_ml.models.artifact import library_versions, write_json
from ultron_health_ml.models.datasets import sha256
from ultron_health_ml.models.training import csv_report
from ultron_health_ml.nuisance.replay import decision_hash, score_partition
from ultron_health_ml.reduced.datasets import new_output, open_lock, prepare, references
from ultron_health_ml.reduced.policy import ENTRY, ReducedReplay, common_signals
from ultron_health_ml.reduced.protocol import METHODS, POINTS, RULES, SCOPE, budget, choose, read_experiment, safety


def partition(root, runs, config, points, copies=1):
    features = FeatureConfig()
    traces = [{point.name: [] for point in points} for _ in range(copies)]
    notices = [{point.name: [] for point in points} for _ in range(copies)]
    measurements = {point.name: [] for point in points}
    for run in runs:
        source = open_telemetry(root, run)
        machines = [{point.name: ReducedReplay(config, point) for point in points} for _ in traces]
        availability = [{point.name: [] for point in points} for _ in traces]
        flags = [{point.name: [] for point in points} for _ in traces]
        times, monitored = [], 0.
        crossings = {key: [] for key in CRITICAL_LIMITS}
        rows = iter(iter_features(source.window, features, dataset_version=source.dataset_version))
        for record in source.window.records:
            begin = perf_counter()
            row = next(rows)
            feature_ms = (perf_counter() - begin) * 1000
            now = row.sample_time.timestamp()
            if times:
                monitored += min(now - times[-1], record.nominal_interval_seconds * 2)
            times.append(now)
            for key, (metric, _, _) in CRITICAL_LIMITS.items():
                if critical_condition(key, row.values.get(f"{metric}__current")):
                    crossings[key].append(now)
            scores = None
            score_ms = 0.
            failed = False

            def read_scores():
                nonlocal scores, score_ms, failed
                if scores is None:
                    started = perf_counter()
                    try:
                        scores = common_signals(row)
                    except Exception:
                        failed, scores = True, {}
                    score_ms = (perf_counter() - started) * 1000
                return scores

            for index, group in enumerate(machines):
                for name, machine in group.items():
                    started = perf_counter()
                    prior_score_ms = score_ms
                    valid, active = machine.consume(row, read_scores)
                    duration = (perf_counter() - started) * 1000 - (score_ms - prior_score_ms)
                    if copies > 1 and index == 0:
                        measurements[name].append(feature_ms + score_ms + duration)
                    machine.failures += failed
                    availability[index][name].append(valid)
                    flags[index][name].append(active)
        for index, group in enumerate(machines):
            for name, machine in group.items():
                traces[index][name].append(Trace(run["run"], times, flags[index][name],
                    availability[index][name], crossings, machine.episodes, monitored, machine.safety_checks,
                    machine.safety_misses, machine.failures))
                notices[index][name].extend(dict(run=run["run"], **asdict(item)) for item in machine.silent)
    labels = {run["run"]: open_events(root, run) for run in runs}
    return traces, notices, labels, measurements


def scoped(traces, notices, labels, config):
    output = score_partition(traces, notices, labels, config)
    for summary, events, _, by_subsystem, _ in output.values():
        for event in events:
            event["in_primary_scope"] = event["subsystem"] in SCOPE
        primary = [row for row in events if row["in_primary_scope"]]
        delays = [row["delay_seconds"] for row in primary if row["detected"]]
        summary.update(global_events=summary["events"], global_detected=summary["detected"],
                       events=len(primary), detected=len(delays), event_recall=len(delays) / len(primary),
                       recall_interval_95=wilson(len(delays), len(primary)),
                       median_delay_seconds=median(delays) if delays else None,
                       p95_delay_seconds=float(np.quantile(delays, .95)) if delays else None,
                       insufficient_scope_rows=sum(by_subsystem[key]["insufficient_rows"] for key in SCOPE),
                       eligible_scope_rows=sum(by_subsystem[key]["eligible_subsystem_rows"] for key in SCOPE))
    return output


def paired(hybrid, comparator):
    left = {(row["run"], row["event_id"]): row for row in hybrid if row["in_primary_scope"]}
    right = {(row["run"], row["event_id"]): row for row in comparator if row["in_primary_scope"]}
    if left.keys() != right.keys():
        raise ValueError("methods did not evaluate the same events")
    runs = sorted({key[0] for key in left})
    pairs = {run: [(row, right[key]) for key, row in left.items() if key[0] == run] for run in runs}

    def measure(rows):
        recall = sum(int(first["detected"]) - int(second["detected"]) for first, second in rows) / len(rows)
        common = [(first["delay_seconds"], second["delay_seconds"]) for first, second in rows
                  if first["detected"] and second["detected"]]
        early = median(first for first, _ in common) if common else None
        late = median(second for _, second in common) if common else None
        return recall, (late - early) / late if late and early is not None else None, len(common), early, late

    all_rows = [pair for rows in pairs.values() for pair in rows]
    gain, delay, common, hybrid_delay, baseline_delay = measure(all_rows)
    rng = np.random.default_rng(45103)
    distribution = [measure([pair for index in sample for pair in pairs[runs[index]]])
                    for sample in rng.integers(0, len(runs), size=(1000, len(runs)))]

    def interval(values):
        return [float(value) for value in np.quantile(values, [.025, .975])] if values else None

    recall_ci = interval([item[0] for item in distribution])
    delay_ci = interval([item[1] for item in distribution if item[1] is not None])
    subsystems = {key: [pair for pair in all_rows if pair[0]["subsystem"] == key] for key in SCOPE}
    per_subsystem = {key: dict(events=len(rows), recall_gain=measure(rows)[0] if rows else None)
                     for key, rows in subsystems.items()}
    sufficient = len(runs) >= 8 and all(item["events"] >= 10 for item in per_subsystem.values())
    no_sub_loss = all(item["recall_gain"] is not None and item["recall_gain"] >= -.10 - 1e-12
                      for item in per_subsystem.values())
    recall_wins = gain >= .10 - 1e-12 and recall_ci[0] > 0
    delay_wins = (delay is not None and delay >= .20 and gain >= -.05 - 1e-12 and common >= 10
                  and delay_ci and delay_ci[0] > 0)
    potential_win = gain >= .10 - 1e-12 or (delay is not None and delay >= .20 and gain >= -.05 - 1e-12)
    state = ("PASS" if sufficient and no_sub_loss and (recall_wins or delay_wins) else
             "INCONCLUSIVE" if not sufficient or (potential_win and no_sub_loss) else "FAIL")
    noninferior = (sufficient and gain <= .10 + 1e-12 and common >= 10 and baseline_delay <= 1.25 * hybrid_delay
                   and all(item["recall_gain"] <= .10 + 1e-12 for item in per_subsystem.values()))
    return dict(value=state, hybrid_recall_gain=gain, paired_recall_interval_95=recall_ci,
                common_hits=common, hybrid_common_median=hybrid_delay, comparator_common_median=baseline_delay,
                hybrid_delay_reduction=delay, paired_delay_interval_95=delay_ci,
                per_subsystem=per_subsystem, simpler_descriptively_noninferior=noninferior)


def assess(chosen, qualified, results, config, replay_equal, p95):
    hybrid = results[chosen["hybrid"].name][0]
    comparisons = []
    for method in METHODS[:-1]:
        result = results[chosen[method].name]
        feasible = qualified[method] and safety(result[0]) and budget(result[0], config)
        useful = feasible and result[0]["event_recall"] >= .20
        comparison = paired(results[chosen["hybrid"].name][1], result[1]) if useful else {}
        comparisons.append(dict(comparator=method, budget_qualified=feasible, useful=useful, **comparison))
    useful = [item for item in comparisons if item["useful"]]
    gates = dict(safety=safety(hybrid) and not hybrid["statistical_failures"],
                 nuisance=qualified["hybrid"] and budget(hybrid, config),
                 incremental_value=bool(useful) and hybrid["event_recall"] >= .50
                 and all(item["value"] == "PASS" for item in useful),
                 reproducibility=replay_equal, performance=p95 < 100)
    decision = ("GO TO PHASE 5 WITH REDUCED SUBSYSTEM SCOPE" if all(gates.values()) else
                "STOP PREDICTIVE PIPELINE" if all(passed for name, passed in gates.items() if name != "incremental_value")
                and any(item["simpler_descriptively_noninferior"] for item in useful) else "REPEAT PHASE 4.5")
    return gates, comparisons, decision


def report_metrics(method, stage, point, summary, config):
    return dict(stage=stage, point=point.name, threshold=point.level, persistence=point.persistence,
                qualifies=safety(summary) and budget(summary, config), **summary_row(method, summary))


def run(inputs, output, config, digest, prior):
    lock = open_lock(inputs, config, digest)
    if output.resolve() == inputs.resolve() or inputs.resolve() in output.resolve().parents:
        raise ValueError("reports cannot be written inside locked inputs")
    new_output(output)
    write_json(output / "scope_definition.json", dict(primary=SCOPE, global_critical=CRITICAL_LIMITS, entry=ENTRY))
    write_json(output / "selection_protocol.json", RULES)
    (output / "phase45c_config.yaml").write_text(yaml.safe_dump(dict(base=config.model_dump(mode="json"),
        experiment=lock["experiment"]), sort_keys=False), encoding="utf-8")
    stages = {}
    for split in ("train", "validation"):
        traces, notices, labels, _ = partition(inputs, [item for item in lock["runs"] if item["split"] == split], config, POINTS)
        stages[split] = scoped(traces[0], notices[0], labels, config)
    dev, validation = ({name: value[0] for name, value in stages[split].items()} for split in ("train", "validation"))
    chosen, qualified, closest = {}, {}, {}
    for method in METHODS:
        chosen[method], qualified[method], closest[method] = choose(
            [point for point in POINTS if point.method == method], dev, validation, config)
    write_json(output / "frozen_selection.json", dict(points={key: asdict(value) for key, value in chosen.items()},
               preholdout_qualified=qualified, closest_validation_overbudget={key: value.name if value else None
               for key, value in closest.items()}, input_manifest_sha256=digest,
               scope_sha256=sha256(output / "scope_definition.json"),
               selection_protocol_sha256=sha256(output / "selection_protocol.json")))
    frozen_hash = sha256(output / "frozen_selection.json")
    holdout = [item for item in lock["runs"] if item["split"] == "holdout"]
    traces, notices, labels, times = partition(inputs, holdout, config, tuple(chosen.values()), copies=2)
    primary, repeated = [scoped(traces[index], notices[index], labels, config) for index in range(2)]
    hashes = [decision_hash(traces[index], notices[index], results) for index, results in enumerate((primary, repeated))]
    timing = times[chosen["hybrid"].name]
    p95 = float(np.quantile(timing, .95))
    gates, comparisons, decision = assess(chosen, qualified, primary, config, hashes[0] == hashes[1], p95)
    repeat_gates = assess(chosen, qualified, repeated, config, hashes[0] == hashes[1], p95)
    if (gates, comparisons, decision) != repeat_gates:
        raise ValueError("deterministic acceptance replay mismatch")

    csv_report(output / "alert_budget_frontier.csv", [report_metrics(point.method, split, point, summary[point.name], config)
               for split, summary in (("development", dev), ("validation", validation)) for point in POINTS])
    operating = []
    for method, point in chosen.items():
        alternatives = [candidate for candidate in POINTS if candidate.method == method and all(
            budget(stage[candidate.name], config) and safety(stage[candidate.name]) for stage in (dev, validation))]
        row = report_metrics(method, "holdout", point, primary[point.name][0], config)
        operating.append(dict(**row, preholdout_qualified=qualified[method],
            closest_overbudget_validation_point=closest[method].name if closest[method] else None,
            maximum_validation_recall_within_budget=max((validation[item.name]["event_recall"]
                                                         for item in alternatives), default=None)))
    csv_report(output / "comparator_operating_points.csv", operating[:-1])
    csv_report(output / "candidate_operating_points.csv", operating[-1:])
    for filename, index in (("event_evaluation.csv", 1), ("episode_evaluation.csv", 2), ("workload_nuisance.csv", 4)):
        csv_report(output / filename, [dict(method=method, **row) for method, point in chosen.items()
                                      for row in primary[point.name][index]])
    csv_report(output / "subsystem_evaluation.csv", [dict(method=method, subsystem=key, primary=key in SCOPE, **row)
               for method, point in chosen.items() for key, row in primary[point.name][3].items()])
    csv_report(output / "critical_safety_evaluation.csv", [dict(method=method, **row)
               for method, point in chosen.items() for row in primary[point.name][1] if row["severity"] == "critical"])
    csv_report(output / "observe_episodes.csv", [dict(point=point, **row) for point, rows in notices[0].items() for row in rows])
    write_json(output / "locked_holdout_manifest.json", dict(input_manifest_sha256=digest, experiment=lock["experiment"],
               runs=holdout, frozen_selection_sha256=frozen_hash))
    write_json(output / "acceptance_gates.json", dict(gates=gates, decision=decision, paired_comparisons=comparisons,
               selected={method: point.name for method, point in chosen.items()},
               summaries={method: primary[point.name][0] for method, point in chosen.items()},
               performance=dict(samples=len(timing), median_ms=median(timing), p95_ms=p95)))
    write_json(output / "reproducibility_manifest.json", dict(reference=prior, feature_schema=FEATURE_SCHEMA_VERSION,
               feature_fingerprint=FeatureConfig().fingerprint(), environment=library_versions(),
               input_manifest=lock, frozen_selection_sha256=frozen_hash, decisions_sha256=hashes[0],
               replicate_sha256=hashes[1]))
    return decision, hashes[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("prepare", "evaluate"))
    parser.add_argument("--config", type=Path, default=Path("ml/configs/phase45c_config.yaml"))
    parser.add_argument("--base", type=Path, default=Path("ml/configs/hybrid_detection.yaml"))
    parser.add_argument("--inputs", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--locked-sha256")
    parser.add_argument("--reference-root", type=Path)
    args = parser.parse_args()
    settings = read_experiment(args.config).replay_config(args.base)
    if args.operation == "prepare":
        print(prepare(args.inputs, settings))
    else:
        if args.output is None or not args.locked_sha256:
            parser.error("evaluate requires --output and --locked-sha256")
        print(*run(args.inputs, args.output, settings, args.locked_sha256, references(args.reference_root, args.base.parent)))


if __name__ == "__main__":
    main()
