"""Run the offline POC; the test split is opened only after validation selection."""
import argparse
from collections import Counter
import csv
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

import numpy as np
import pandas as pd
import yaml

from ultron_health_ml.features.config import load_config as load_features_config
from ultron_health_ml.features.validation import load_evaluation_labels
from ultron_health_ml.models.artifact import load_model_artifact, save_model_artifact, seal, write_json
from ultron_health_ml.models.baselines import mad_baseline, threshold_baseline
from ultron_health_ml.models.config import MODEL_VERSION, load_config
from ultron_health_ml.models.datasets import generate_run
from ultron_health_ml.models.evaluation import Truth, align_truth, choose_threshold, evaluate, observability
from ultron_health_ml.models.isolation_forest import FeatureSignature, fit_model


def csv_report(path, rows):
    with path.open("w", newline="", encoding="utf-8") as stream:
        if rows:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)


def select_model(train, validation, signature, metrics, settings):
    training_frame = pd.concat([run.frame for run in train], ignore_index=True)
    visibility = observability(metrics)
    candidates = []
    truth = None
    for index, candidate in enumerate(settings.candidates):
        start = perf_counter()
        timings = {}
        model = fit_model(training_frame, signature, settings, candidate, timings=timings)
        fit_seconds = perf_counter() - start
        scores = np.concatenate([model.score_frame(run.frame, signature) for run in validation])
        if truth is None:
            labels = [align_truth(run, load_evaluation_labels(run.directory), visibility) for run in validation]
            truth = Truth(*(np.concatenate([getattr(item, key) for item in labels])
                            for key in ("actual", "scope", "workload")))
        summary = choose_threshold(scores, truth, settings)
        candidates.append((replace(model, threshold=summary["threshold"]),
                           dict(candidate=index, **candidate.model_dump(), fit_seconds=fit_seconds, **timings, **summary)))
    model, selected = max(candidates, key=lambda pair: (
        pair[1]["objective"], -(pair[1]["workload_fpr"] or 0), -(pair[1]["fpr"] or 0),
        not pair[1]["scale"], -pair[1]["candidate"]
    ))
    return model, selected, [dict(summary, selected=summary is selected) for _, summary in candidates]


def check_distinct_runs(runs):
    ids, hashes = set(), set()
    for run in runs:
        if ids.intersection(run.ids) or run.telemetry_hash in hashes:
            raise ValueError("duplicate sample IDs or telemetry across independent runs")
        ids.update(run.ids)
        hashes.add(run.telemetry_hash)


def comparison_row(name, result):
    return dict(method=name, **result["profile_scope"],
                episodes=result["episodes"]["total"], false_episodes=result["episodes"]["false"],
                alerts_per_day=result["episodes"]["alerts_per_day"],
                false_alerts_per_day=result["episodes"]["false_alerts_per_day"],
                incident_detection=result["incidents"]["detection_rate"],
                backup_fpr=result["workloads"]["BACKUP_WORKLOAD"]["fpr"],
                build_fpr=result["workloads"]["COMPILATION_OR_BUILD_WORKLOAD"]["fpr"])


def report_text(evaluation, comparisons, selected, scenarios):
    def display(value):
        return "n/a" if value is None else f"{value:.3f}"

    lines = ["# ULTRON Phase 4: Offline anomaly detection", "",
             "Synthetic anomaly detection performance is not evidence of real hardware-failure prediction.", "",
             f"Profile: `{evaluation['profile']}`. Model version: `{MODEL_VERSION}`.",
             f"Threshold: `{selected['threshold']:.12g}` (higher scores are more unusual, not probabilities).",
             f"Features: {evaluation['feature_audit']['selected']} of {evaluation['feature_audit']['input']} retained.",
             "", "## Held-out comparison (profile scope)", "",
             "| Method | Precision | Recall | F1 | FPR | Alerts/day | False alerts/day | Incident detection | Backup FPR | Build FPR |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    keys = ("precision", "recall", "f1", "fpr", "alerts_per_day", "false_alerts_per_day",
            "incident_detection", "backup_fpr", "build_fpr")
    lines.extend("| " + " | ".join([row["method"], *(display(row[key]) for key in keys)]) + " |"
                 for row in comparisons)
    lines.extend(["", "## Scenario coverage", "",
                  "Counts show alerted/evaluable instances. Backup/build alerts are false positives.",
                  "", "| Scenario | Visibility | Threshold | MAD | Isolation Forest |",
                  "|---|---|---:|---:|---:|"])
    for name, visible in evaluation["observability"].items():
        counts = [evaluation["methods"][key]["scenarios"].get(name) for key in
                  ("threshold", "mad", "isolation_forest")]
        lines.append("| " + " | ".join([name, visible, *[
            f"{row['alerted']}/{row['detectable_instances']}" if row and row["detectable_instances"] else "n/a"
            for row in counts]]) + " |")
    lines.extend(["", "## Audit", "",
                  "Model and cutoff selected using validation only. Test labels opened after all test scores were computed.",
                  "See evaluation.json for incident, workload and episode statistics; CSVs give per-instance timestamps and delays.",
                  f"Reload parity: {evaluation['parity']['max_abs_score_delta']:.2g} maximum score difference.",
                  f"Scenario evaluations: {len(scenarios)} (all methods).", ""])
    return "\n".join(lines)


def train(settings, config, datasets, output):
    for path in (datasets, output):
        if path.exists() and any(path.iterdir()):
            raise ValueError(f"output directory must be empty: {path}")
    if datasets.resolve() == output.resolve() or datasets.resolve() in output.resolve().parents or output.resolve() in datasets.resolve().parents:
        raise ValueError("dataset and artifact directories must be separate")
    signature = FeatureSignature.from_config(config)
    metrics = config.selected_metrics()
    start = perf_counter()

    def build(split, seeds):
        return [generate_run(datasets, split, seed, settings.days_per_run, config) for seed in seeds]

    training = build("train", settings.train_seeds)
    validation = build("validation", settings.validation_seeds)
    development_data_seconds = perf_counter() - start
    check_distinct_runs([*training, *validation])
    started = perf_counter()
    model, selected, experiments = select_model(training, validation, signature, metrics, settings)
    selection_seconds = perf_counter() - started

    started = perf_counter()
    test = build("test", settings.test_seeds)
    test_data_seconds = perf_counter() - started
    runs = [*training, *validation, *test]
    check_distinct_runs(runs)
    scored = {"threshold": [], "mad": [], "isolation_forest": []}
    started = perf_counter()
    for run in test:
        scores = model.score_frame(run.frame, signature)
        scored["isolation_forest"].append((run, scores, scores >= model.threshold))
    score_seconds = perf_counter() - started
    started = perf_counter()
    for run in test:
        scored["threshold"].append((run, *threshold_baseline(run, settings)))
        scored["mad"].append((run, *mad_baseline(run, settings)))
    labels = [load_evaluation_labels(run.directory) for run in test]
    methods, scenario_rows, episode_rows, comparisons = {}, [], [], []
    for name, entries in scored.items():
        result, scenarios, episodes = evaluate(entries, labels, metrics, settings)
        methods[name] = result
        scenario_rows.extend(dict(method=name, **row) for row in scenarios)
        episode_rows.extend(dict(method=name, **row) for row in episodes)
        comparisons.append(comparison_row(name, result))
    preprocessing = model.preprocessor
    evaluation = dict(profile=config.profile, observability=observability(metrics), methods=methods,
                      validation_selection=selected, feature_audit=dict(input=len(signature.model_columns),
                      selected=len(preprocessing.selected), dropped_by_reason=dict(Counter(preprocessing.dropped.values())),
                      retained_quality_indicators=[name for name in preprocessing.selected if name.endswith(
                          ("__available", "__fresh", "__cached", "__stale"))]),
                      timing=dict(development_data_seconds=development_data_seconds, test_data_seconds=test_data_seconds,
                                  feature_load_seconds=sum(run.feature_load_seconds for run in runs),
                                  selection_seconds=selection_seconds, evaluation_seconds=perf_counter() - started,
                                  test_batch_score_seconds=score_seconds))
    splits = [dict(run=run.name, seed=run.seed, split=run.split, rows=len(run.ids),
                   first_sample=run.times[0].isoformat(), last_sample=run.times[-1].isoformat(),
                   telemetry_sha256=run.telemetry_hash, features_sha256=run.feature_hash) for run in runs]
    metadata = dict(created_at=datetime.now(timezone.utc).isoformat(), random_seed=settings.seed,
                    hyperparameters=model.estimator.get_params(), scaled=selected["scale"], runs=splits)
    save_model_artifact(output, model, metadata, evaluation, yaml.safe_dump({
        "training": settings.model_dump(mode="json"), "features": config.model_dump(mode="json"),
        "signature": {**asdict(signature), "model_columns": list(signature.model_columns)},
    }, sort_keys=False))
    reloaded = load_model_artifact(output, signature, trusted=True)
    max_delta = 0.0
    for run, scores, decisions in scored["isolation_forest"]:
        restored = reloaded.score_frame(run.frame, signature)
        np.testing.assert_allclose(restored, scores, rtol=0, atol=1e-12)
        np.testing.assert_array_equal(restored >= reloaded.threshold, decisions)
        max_delta = max(max_delta, float(np.max(np.abs(restored - scores))))
    frame = test[0].frame.iloc[[0]]
    started = perf_counter()
    for _ in range(5):
        single = reloaded.score_frame(frame, signature)
    evaluation["timing"]["mean_single_row_seconds"] = (perf_counter() - started) / 5
    np.testing.assert_allclose(single, scored["isolation_forest"][0][1][:1], atol=1e-12, rtol=0)
    evaluation["parity"] = dict(max_abs_score_delta=max_delta, tolerance=1e-12, decisions_identical=True)
    evaluation["model_bytes"] = (output / "model.joblib").stat().st_size
    write_json(output / "evaluation.json", evaluation)
    seal(output)
    write_json(output / "splits.json", splits)
    write_json(output / "experiments.json", experiments)
    csv_report(output / "experiment_results.csv", experiments)
    csv_report(output / "scenario_evaluation.csv", scenario_rows)
    csv_report(output / "episode_evaluation.csv", episode_rows)
    csv_report(output / "baseline_comparison.csv", comparisons)
    csv_report(output / "split_membership.csv", [dict(run=run.name, split=run.split, sample_id=sample_id,
                                                     sample_time=stamp.isoformat())
                                                 for run in runs for sample_id, stamp in zip(run.ids, run.times)])
    (output / "report.md").write_text(report_text(evaluation, comparisons, selected, scenario_rows), encoding="utf-8")
    return evaluation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--feature-config", type=Path)
    parser.add_argument("--profile", choices=["real_ultron", "synthetic_extended"], default="real_ultron")
    parser.add_argument("--datasets", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    train(load_config(args.config), load_features_config(args.feature_config, profile=args.profile),
          args.datasets, args.output)
    print(f"Offline report: {args.output / 'report.md'}")


if __name__ == "__main__":
    main()
