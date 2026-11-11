"""Run predeclared ablations and score a new locked holdout exactly once."""

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
from ultron_health_ml.hybrid.config import CRITICAL_LIMITS, WORKLOADS
from ultron_health_ml.hybrid.datasets import open_events, open_telemetry
from ultron_health_ml.hybrid.detector import critical_condition
from ultron_health_ml.hybrid.evaluation import Trace, evaluate, wilson
from ultron_health_ml.hybrid.replay import summary_row, trace_sha
from ultron_health_ml.models.artifact import library_versions, write_json
from ultron_health_ml.models.datasets import sha256
from ultron_health_ml.models.training import csv_report
from ultron_health_ml.nuisance.datasets import contract, locked, new_output, prepare
from ultron_health_ml.nuisance.policy import PolicyReplay
from ultron_health_ml.nuisance.protocol import CANDIDATES, COMPARATORS, OBJECTIVE, POLICIES, budget, objective, read_experiment, safety

SCOPE = {
    "cpu": ("PRODUCTION CANDIDATE", "needs corroborating high load; coincidence is not causation"),
    "memory": ("PRODUCTION CANDIDATE", "signed growth, headroom and stricter evidence at high load"),
    "disk_capacity": ("PRODUCTION CANDIDATE", "one fixed volume; gradual decline limited by fresh cadence"),
    "disk_health": ("ADVISORY ONLY", "temperature only; no SMART, wear, or failure inference"),
    "battery_drain": ("EXPERIMENTAL", "unplugged observe only in the scoped candidate"),
    "battery_capacity": ("DISABLED", "coverage only; no capacity degradation claim"),
    "process": ("PRODUCTION CANDIDATE", "corroboration or sustained nonresponse; associated with CPU when overlapping"),
    "events": ("PRODUCTION CANDIDATE", "bounded event count only; no cause inference"),
}


def read_references(repo, configs):
    results = {}
    for phase, file in (("phase4", "phase4_reference_sha256.json"), ("phase45a", "phase45a_reference_sha256.json")):
        expected = json.loads((configs / file).read_text(encoding="utf-8"))
        if repo is not None:
            for name, digest in expected.items():
                if sha256(repo / name) != digest:
                    raise ValueError(f"frozen reference changed: {name}")
        results[phase] = dict(files_verified=len(expected) if repo is not None else 0,
                              manifest_sha256=sha256(configs / file), hashes=expected)
    return results


def run_partition(root, runs, config, *, double=False):
    feature = FeatureConfig(profile="real_ultron")
    replicas = [{name: [] for name in POLICIES} for _ in range(2 if double else 1)]
    observations = [{name: [] for name in POLICIES} for _ in replicas]
    measurements = {name: [] for name in POLICIES}
    for run in runs:
        source = open_telemetry(root, run)
        machines = [{name: PolicyReplay(config, name) for name in POLICIES} for _ in replicas]
        times, coverage = [], 0.
        crossings = {key: [] for key in CRITICAL_LIMITS}
        flags = [{name: [] for name in POLICIES} for _ in replicas]
        availability = [{name: [] for name in POLICIES} for _ in replicas]
        phase3 = iter(iter_features(source.window, feature, dataset_version=source.dataset_version))
        for record in source.window.records:
            begin = perf_counter()
            row = next(phase3)
            feature_ms = (perf_counter() - begin) * 1000
            now = row.sample_time.timestamp()
            if times:
                coverage += min(now - times[-1], 2 * record.nominal_interval_seconds)
            times.append(now)
            for key, (metric, _, _) in CRITICAL_LIMITS.items():
                if critical_condition(key, row.values.get(f"{metric}__current")):
                    crossings[key].append(now)
            for replica, group in enumerate(machines):
                for name, machine in group.items():
                    begin = perf_counter()
                    valid, active = machine.step(row)
                    if double and replica == 0:
                        measurements[name].append(feature_ms + (perf_counter() - begin) * 1000)
                    flags[replica][name].append(active)
                    availability[replica][name].append(valid)
        for replica, group in enumerate(machines):
            for name, machine in group.items():
                replicas[replica][name].append(Trace(run["run"], times, flags[replica][name],
                    availability[replica][name], crossings, machine.episodes, coverage, machine.safety_checks,
                    machine.safety_misses, machine.failures))
                observations[replica][name].extend(dict(run=run["run"], **asdict(notice)) for notice in machine.silent)
    labels = {run["run"]: open_events(root, run) for run in runs}
    return replicas, observations, labels, measurements


def score_partition(traces, observes, labels, config):
    output = {}
    for name, batches in traces.items():
        result, event_rows, episodes, by_system, sessions = evaluate(batches, labels, config)
        false_critical = [row for row in episodes if row["critical_at"] is not None and not any(
            event.actionable_onset is not None and event.subsystem == row["subsystem"]
            and event.actionable_onset <= row["critical_at"] < event.event_end for event in labels[row["run"]]
        )]
        advisory_false = result["false_noncritical_episodes"]
        false_critical_only = [row for row in false_critical if not row["false_noncritical"]]
        for row in episodes:
            row["false_critical"] = row in false_critical
        for session, event in zip(sessions, (event for batch in batches for event in labels[batch.name]
                                             if event.kind in WORKLOADS), strict=True):
            session["nuisance"] |= any(row["run"] == session["run"]
                                        and event.event_start <= row["critical_at"] < event.event_end
                                        for row in false_critical)
        nuisance = sum(row["nuisance"] for row in sessions)
        result.update(false_warning_episodes=advisory_false, false_critical_episodes=len(false_critical),
                      false_episodes_per_day=(advisory_false + len(false_critical_only)) / result["monitored_device_days"],
                      nuisance_sessions=nuisance, workload_nuisance_rate=nuisance / len(sessions) if sessions else None,
                      workload_interval_95=wilson(nuisance, len(sessions)),
                      observe_episodes=len(observes[name]), interrupting_episodes=len(episodes),
                      all_episodes=len(episodes) + len(observes[name]),
                      associated_cpu_episodes=sum("associated_process_nonresponse" in row["detector_sources"]
                                                  for row in episodes))
        for key, summary in by_system.items():
            summary["observe_episodes"] = sum(row["subsystem"] == key for row in observes[name])
        output[name] = (result, event_rows, episodes, by_system, sessions)
    return output


def decision_hash(traces, observes, results):
    data = dict(traces=trace_sha(traces), observes=observes, scores=results)
    return hashlib.sha256(json.dumps(data, sort_keys=True, allow_nan=False).encode()).hexdigest()


def pick(development, validation, config):
    rank = sorted(CANDIDATES, key=lambda name: objective(development[name][0], config), reverse=True)
    cutoff = objective(development[rank[min(3, len(rank) - 1)]][0], config)
    shortlist = [name for name in rank if objective(development[name][0], config) >= cutoff]
    chosen = max(shortlist, key=lambda name: objective(validation[name][0], config))
    qualified = budget(validation[chosen][0], config) and safety(validation[chosen][0])
    return chosen, shortlist, qualified


def acceptance(result, comparisons, config, parity, p95):
    eligible = [row for row in comparisons if safety(row) and budget(row, config)]
    detection = (result["events"] >= 1 and result["event_recall"] >= .50 and bool(eligible)
                 and all(result["detected"] >= row["detected"] - 1 for row in eligible))
    return dict(safety=safety(result) and result["critical_expected"] > 0, nuisance=budget(result, config),
                detection=detection, reproducibility=parity, performance=p95 < 100)


def evaluate_locked(inputs, output, config, digest, refs):
    lock = locked(inputs, config, digest)
    if inputs.resolve() == output.resolve() or inputs.resolve() in output.resolve().parents:
        raise ValueError("report output cannot be inside locked inputs")
    new_output(output)
    (output / "phase45b_config.yaml").write_text(yaml.safe_dump({
        "effective_base": config.model_dump(mode="json"), "policies": POLICIES,
        "multipliers": dict.fromkeys(POLICIES, 2.), "policy_contract": contract(),
    }, sort_keys=False), encoding="utf-8")
    write_json(output / "selection_objective.json", OBJECTIVE)
    stages = {}
    for split in ("train", "validation"):
        traces, observes, labels, _ = run_partition(inputs, [run for run in lock["runs"] if run["split"] == split], config)
        stages[split] = score_partition(traces[0], observes[0], labels, config)
    chosen, shortlist, qualified = pick(stages["train"], stages["validation"], config)
    write_json(output / "frozen_selection.json", dict(policy=chosen, shortlisted=shortlist, development_eligible=qualified,
               locked_input_sha256=digest, objective_sha256=sha256(output / "selection_objective.json")))

    holdout = [run for run in lock["runs"] if run["split"] == "holdout"]
    traces, observes, labels, measurements = run_partition(inputs, holdout, config, double=True)
    stage = score_partition(traces[0], observes[0], labels, config)
    second = score_partition(traces[1], observes[1], labels, config)
    first_hash = decision_hash(traces[0], observes[0], stage)
    second_hash = decision_hash(traces[1], observes[1], second)
    stages["holdout"] = stage
    result = stage[chosen][0]
    p95 = float(np.quantile(measurements[chosen], .95))
    gates = acceptance(result, [stage[name][0] for name in COMPARATORS], config, first_hash == second_hash, p95)
    gates["development_eligible"] = qualified
    matrix = []
    for key, info in stage[chosen][3].items():
        status, evidence = SCOPE[key]
        if status == "PRODUCTION CANDIDATE" and (info["events"] < 3 or info["event_recall"] < .6
                                                 or info["false_noncritical_episodes"]):
            status = "EXPERIMENTAL"
        matrix.append(dict(subsystem=key, status=status, events=info["events"], detected=info["detected"],
                           recall=info["event_recall"], false_advisories=info["false_noncritical_episodes"],
                           median_delay_seconds=info["median_delay_seconds"], evidence_quality=evidence,
                           recommendation="offline candidate only" if status == "PRODUCTION CANDIDATE"
                           else "do not promote this subsystem"))
    # A reduced scope needs its own locked replay; don't infer that it passes by deleting rows.
    decision = ("GO TO PHASE 5 WITH REDUCED SUBSYSTEM SCOPE" if all(gates.values())
                and chosen.startswith("core") and all(row["status"] == "PRODUCTION CANDIDATE"
                for row in matrix if row["subsystem"] not in {"battery_drain", "battery_capacity", "disk_health"})
                else "REPEAT PHASE 4.5")
    write_json(output / "acceptance_gates.json", dict(decision=decision, selected=chosen, gates=gates, performance_p95_ms=p95,
               performance_median_ms=float(np.median(measurements[chosen])), holdout_summary=result, matrix=matrix))
    rows = [dict(split=split, **summary_row(name, metrics[0]))
            for split, methods in stages.items() for name, metrics in methods.items()]
    csv_report(output / "candidate_ablation.csv", rows)
    csv_report(output / "signal_ablation.csv", [row for row in rows if row["method"] in
               {"sustained_baseline", "core", "core_mad", "core_ewma", "core_cusum"}])
    csv_report(output / "subsystem_ablation.csv", [dict(split=split, method=name, subsystem=key, **metrics)
               for split, methods in stages.items() for name, summary in methods.items()
               for key, metrics in summary[3].items()])
    csv_report(output / "severity_evaluation.csv", [dict(method=name, observe=len(observes[0][name]),
               warning=sum(row["severity"] == "warning" for row in values[2]),
               critical=sum(row["severity"] == "critical" for row in values[2]),
               warning_openings=sum(row["opened_at"] != row["critical_at"] for row in values[2]),
               critical_notifications=sum(row["critical_at"] is not None for row in values[2]),
               all_episodes=values[0]["all_episodes"]) for name, values in stage.items()])
    for filename, index in (("event_evaluation.csv", 1), ("episode_evaluation.csv", 2), ("workload_nuisance.csv", 4)):
        csv_report(output / filename, [dict(method=name, **row) for name, values in stage.items() for row in values[index]])
    csv_report(output / "observe_episodes.csv", [dict(method=name, **row) for name, notices in observes[0].items() for row in notices])
    csv_report(output / "subsystem_matrix.csv", matrix)
    write_json(output / "locked_holdout_manifest.json", dict(sha256=digest, policy=lock["policy"], runs=holdout))
    write_json(output / "reproducibility_manifest.json", dict(reference=refs, input_manifest=lock, feature_schema=FEATURE_SCHEMA_VERSION,
               feature_fingerprint=FeatureConfig().fingerprint(), environment=library_versions(),
               frozen_selection_sha256=sha256(output / "frozen_selection.json"), decisions_sha256=first_hash,
               replicate_sha256=second_hash))
    return decision, chosen, first_hash


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("prepare", "evaluate"))
    parser.add_argument("--config", type=Path, default=Path("ml/configs/phase45b_config.yaml"))
    parser.add_argument("--base", type=Path, default=Path("ml/configs/hybrid_detection.yaml"))
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--locked-sha256")
    parser.add_argument("--reference-root", type=Path)
    args = parser.parse_args()
    experiment = read_experiment(args.config)
    config = experiment.replay_config(args.base)
    if args.operation == "prepare":
        print(prepare(args.inputs, config))
    else:
        if args.output is None or args.locked_sha256 is None:
            parser.error("evaluate requires --output and --locked-sha256")
        refs = read_references(args.reference_root, args.base.parent)
        print(*evaluate_locked(args.inputs, args.output, config, args.locked_sha256, refs))


if __name__ == "__main__":
    main()
