"""Prepare a locked synthetic holdout, then evaluate offline without tuning on it."""

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from time import perf_counter

import numpy as np
import yaml

from ultron_health_ml.features.config import FEATURE_SCHEMA_VERSION, FeatureConfig
from ultron_health_ml.features.engineering import iter_features
from ultron_health_ml.hybrid.config import CRITICAL_LIMITS, PRIMARY_MODES, SCHEMA_VERSION, load_settings
from ultron_health_ml.hybrid.datasets import (
    FAMILIES, KNOWN_PHASE4, PROTOCOL, empty_directory, open_events, open_telemetry, prepare, verify_lock,
)
from ultron_health_ml.hybrid.detector import Replay, critical_condition
from ultron_health_ml.hybrid.evaluation import Trace, choose_candidate, compare, evaluate
from ultron_health_ml.models.artifact import library_versions, write_json
from ultron_health_ml.models.datasets import sha256
from ultron_health_ml.models.training import csv_report

BASELINES = ("fixed_budget", "mad_budget")
ORIGINALS = ("fixed_original", "mad_original")
MODES = (*PRIMARY_MODES, *BASELINES)


def reference_hashes(root, manifest_file):
    expected = json.loads(manifest_file.read_text(encoding="utf-8"))
    if root is not None:
        for name, digest in expected.items():
            if sha256(root / name) != digest:
                raise ValueError(f"frozen Phase 4 reference changed: {name}")
    return dict(commit=KNOWN_PHASE4, reference_hashes=expected, files_verified=len(expected) if root is not None else 0)


def run_replay(source, name, config, features, candidates):
    replays = {key: Replay(config, *key) for key in candidates}
    states = {key: dict(times=[], flags=[], available=[], timings_ms=[]) for key in candidates}
    monitored = 0.
    last_time = None
    expected = {key: [] for key in CRITICAL_LIMITS}
    rows = iter(iter_features(source.window, features, dataset_version=source.dataset_version))
    for _ in source.window.records:
        started = perf_counter()
        row = next(rows)
        feature_ms = (perf_counter() - started) * 1000
        time = row.sample_time.timestamp()
        if last_time is not None:
            monitored += min(time - last_time, 2 * source.window.records[0].nominal_interval_seconds)
        last_time = time
        for key, (metric, _, _) in CRITICAL_LIMITS.items():
            if critical_condition(key, row.values.get(f"{metric}__current")):
                expected[key].append(time)
        for key, replay in replays.items():
            started = perf_counter()
            availability, flags = replay.step(row)
            results = states[key]
            results["timings_ms"].append(feature_ms + (perf_counter() - started) * 1000)
            results["times"].append(time)
            results["flags"].append(flags)
            results["available"].append(availability)
    return {key: Trace(name, critical_observed=expected, episodes=replay.episodes, monitored_seconds=monitored,
                       safety_checks=replay.safety_checks, safety_misses=replay.safety_misses,
                       failures=replay.failures, **states[key]) for key, replay in replays.items()}


def evaluate_split(root, runs, config, features, candidates):
    results = {candidate: [] for candidate in candidates}
    for run in runs:
        source = open_telemetry(root, run)
        for key, trace in run_replay(source, run["run"], config, features, candidates).items():
            results[key].append(trace)
    labels = {run["run"]: open_events(root, run) for run in runs}
    return results, labels


def trace_sha(traces):
    digest = hashlib.sha256()
    for mode, values in sorted(traces.items()):
        for trace in values:
            data = dict(mode=mode, run=trace.name, times=trace.times, flags=[sorted(item) for item in trace.flags],
                        available=trace.available, episodes=[{**asdict(episode),
                        "detector_sources": sorted(episode.detector_sources)} for episode in trace.episodes])
            digest.update(json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())
    return digest.hexdigest()


def summary_row(method, summary):
    return {"method": method, **{key: value if not isinstance(value, (dict, list)) else json.dumps(value)
                                  for key, value in summary.items()}}


def evaluate_locked(root, output, config, expected_sha, phase4):
    lock = verify_lock(root, config, expected_sha)
    if root.resolve() == output.resolve() or root.resolve() in output.resolve().parents:
        raise ValueError("reports must not be written inside locked input")
    empty_directory(output)
    features = FeatureConfig(profile="real_ultron")
    train = [run for run in lock["runs"] if run["split"] == "train"]
    validation = [run for run in lock["runs"] if run["split"] == "validation"]
    holdout = [run for run in lock["runs"] if run["split"] == "holdout"]
    choices = tuple((mode, value) for mode in MODES for value in config.candidates)
    train_traces, train_labels = evaluate_split(root, train, config, features, choices)
    validation_traces, validation_labels = evaluate_split(root, validation, config, features, choices)
    labels = train_labels | validation_labels
    candidate_summaries = {}
    candidate_rows = []
    for mode, value in choices:
        key = (mode, value)
        result = evaluate([*train_traces[key], *validation_traces[key]], labels, config)[0]
        candidate_summaries[key] = result
        candidate_rows.append(dict(multiplier=value, **summary_row(mode, result)))
    selected = {mode: choose_candidate([(value, candidate_summaries[mode, value])
                                        for value in config.candidates], config) for mode in MODES}
    selected.update(dict.fromkeys(ORIGINALS, 1.))
    csv_report(output / "development_candidates.csv", candidate_rows)
    write_json(output / "frozen_selection.json", dict(locked_inputs_sha256=expected_sha,
               feature_config_fingerprint=features.fingerprint(), multipliers=selected,
               validation_protocol="Minimize excess false episodes then workload nuisance; within budget maximize recall, then minimize delay."))
    frozen_selection_hash = sha256(output / "frozen_selection.json")

    traces, labels = evaluate_split(root, holdout, config, features, tuple(selected.items()))
    results, events, episodes, subsystems, workloads, per_run = {}, [], [], [], [], []
    for (mode, _), method_traces in traces.items():
        result, event_rows, episode_rows, by_subsystem, exposure = evaluate(method_traces, labels, config)
        results[mode] = result
        events.extend(dict(method=mode, **row) for row in event_rows)
        episodes.extend(dict(method=mode, **row) | {"evidence": json.dumps(row["evidence"], sort_keys=True)}
                        for row in episode_rows)
        subsystems.extend(dict(method=mode, subsystem=key, **row) for key, row in by_subsystem.items())
        workloads.extend(dict(method=mode, **row) for row in exposure)
        per_run.extend(dict(run=trace.name, **summary_row(mode, evaluate([trace], labels, config)[0]))
                       for trace in method_traces)
    comparisons = [dict(baseline=mode, **compare(results["hybrid"], results[mode], config)) for mode in BASELINES]
    hybrid = results["hybrid"]
    timings = [elapsed for trace in traces["hybrid", selected["hybrid"]] for elapsed in trace.timings_ms]
    safety = hybrid["safety_checks"] > 0 and not any(
        hybrid[key] for key in ("critical_missed", "critical_delayed", "safety_misses"))
    nuisance = (hybrid["false_episodes_per_day"] <= config.false_budget_per_day
                and hybrid["workload_nuisance_rate"] is not None
                and hybrid["workload_nuisance_rate"] <= config.workload_budget)
    incremental = all(row["meets_incremental_gate"] for row in comparisons)
    performance = dict(samples=len(timings), median_ms=float(np.median(timings)),
                       p95_ms=float(np.quantile(timings, .95)),
                       operation="Incremental Phase 3 transform plus hybrid step, excluding collection and file IO.")
    gates = dict(safety=safety, incremental=incremental, nuisance=nuisance, performance=performance["p95_ms"] < 100)
    decision = ("GO TO PHASE 5 WITH LIMITATIONS" if all(gates.values()) else "REPEAT PHASE 4.5")
    output_hash = trace_sha(traces)
    write_json(output / "evaluation.json", dict(methods=results, comparison=comparisons, selected=selected,
               gates=gates, decision=decision, performance=performance,
               optional_ml="not evaluated", decisions_sha256=output_hash))
    write_json(output / "event_definitions.json", dict(
        protocol=PROTOCOL, families=FAMILIES, timestamp_unit="UTC Unix seconds",
        matching="Same subsystem notice from actionable onset inclusive to event end exclusive, each episode credited at most once.",
        row_labels="Rows in any actionable window; any active detector flag is a positive row decision.",
        critical_policy="Check against first valid observed crossing at or after latent incident; separately count unobservable incidents.",
        false_episode_policy="Count advisory openings outside same-subsystem actionable windows, even if later upgraded.",
        recovery_time_policy="Latent reset is at injection end; locked label recovery_time is the separate observation deadline.",
        monitored_time="Sum adjacent sample gaps capped at twice nominal cadence, excluding unobserved sleep gaps.",
        flap_policy="New episode within configured interval of a valid same-subsystem closure.",
        events={name: [{**asdict(event), "recovery_time": event.event_end,
                        "recovery_deadline": event.recovery_time} for event in items]
                for name, items in labels.items()},
    ))
    (output / "hybrid_config.yaml").write_text(yaml.safe_dump(config.model_dump(mode="json"), sort_keys=False),
                                                encoding="utf-8")
    csv_report(output / "event_evaluation.csv", events)
    csv_report(output / "episode_evaluation.csv", episodes)
    csv_report(output / "subsystem_evaluation.csv", subsystems)
    csv_report(output / "workload_sessions.csv", workloads)
    csv_report(output / "per_run_evaluation.csv", per_run)
    csv_report(output / "alert_budget_comparison.csv", comparisons)
    csv_report(output / "detector_comparison.csv", [summary_row(mode, result) for mode, result in results.items()])
    write_json(output / "locked_holdout_manifest.json",
               dict(protocol=PROTOCOL, locked_inputs_sha256=expected_sha, runs=holdout))
    write_json(output / "reproducibility_manifest.json", dict(
        detector_schema=SCHEMA_VERSION, telemetry_schema="1.0.0", feature_schema=FEATURE_SCHEMA_VERSION,
        feature_config=features.model_dump(mode="json"), feature_fingerprint=features.fingerprint(),
        runtime=library_versions(), frozen_phase4=phase4, locked_inputs=lock, locked_inputs_sha256=expected_sha,
        frozen_selection_sha256=frozen_selection_hash, decisions_sha256=output_hash,
        event_definition_sha256=sha256(output / "event_definitions.json"),
        configuration_sha256=sha256(output / "hybrid_config.yaml"),
        detector_source_sha256={path.name: sha256(path) for path in sorted(Path(__file__).parent.glob("*.py"))},
    ))
    return decision, output_hash


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("prepare", "evaluate"))
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--locked-sha256")
    parser.add_argument("--phase4-reference-root", type=Path)
    parser.add_argument("--reference-manifest", type=Path, default=Path("ml/configs/phase4_reference_sha256.json"))
    args = parser.parse_args()
    config = load_settings(args.config)
    if args.operation == "prepare":
        prepare(args.inputs, config)
        print(sha256(args.inputs / "locked_inputs.json"))
    else:
        if args.output is None or args.locked_sha256 is None:
            parser.error("evaluate requires --output and --locked-sha256")
        reference = reference_hashes(args.phase4_reference_root, args.reference_manifest)
        print(*evaluate_locked(args.inputs, args.output, config, args.locked_sha256, reference))


if __name__ == "__main__":
    main()
