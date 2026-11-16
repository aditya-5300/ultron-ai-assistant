"""Summarize all five paired contention runs and isolated reliability evidence."""

import argparse
import csv
import hashlib
import json
import math
import platform
from pathlib import Path


def read_tap(path, prefix, key, require_success=True):
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    if require_success and "# fail 0" not in lines:
        raise ValueError(f"Expected successful TAP: {path.name}")
    pending, records = None, {}
    for line in lines:
        if line.startswith(prefix):
            pending = line[len(prefix):]
        elif pending is not None and line.startswith("# "):
            pending += line[2:]
        else:
            continue
        try:
            item = json.loads(pending)
        except json.JSONDecodeError:
            continue
        records[item[key]] = item.get("value", item.get("data"))
        pending = None
    return records


def metric(rows, key, fraction):
    values = sorted(row[key] for row in rows if row.get(key) is not None)
    return round(values[math.ceil(len(values) * fraction) - 1], 4) if values else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("logs", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    config = Path(__file__).resolve().parents[1] / "docs/phase8d-reference/phase8d_config.json"
    settings = json.loads(config.read_text())
    def tap(name, prefix, key):
        return read_tap(args.logs / f"ultron-8d-{name}.tap", prefix, key)

    benchmark = tap("benchmark", "# PHASE8D ", "kind")
    collector = tap("collector", "# PHASE8C ", "kind")
    admissions = tap("collector", "# PHASE8D ", "kind")
    residual = tap("residual", "# PHASE8D ", "kind")["residual"]
    ownership = tap("replay", "# PHASE8 ", "kind")
    operational_path = args.logs / "ultron-8d-operational.tap"
    operational = read_tap(operational_path, "# PHASE8B ", "key", require_success=False)
    diagnostic_path = args.logs / "ultron-8d-legacy-diagnostic.tap"
    operational_text = operational_path.read_text(encoding="utf-8-sig")
    failed_tests = [line for line in operational_text.splitlines() if line.startswith("not ok ")]
    expected_legacy = "actual Windows collector: refusal, clean shutdown, abrupt restart and persisted recovery"
    diagnostic_text = diagnostic_path.read_text(encoding="utf-8-sig") if diagnostic_path.exists() else ""
    legacy_recheck = "# fail 0" in diagnostic_text and ("ok 1 - " + expected_legacy) in diagnostic_text
    operational_passed = "# fail 0" in operational_text or (
        len(failed_tests) == 1 and failed_tests[0].endswith(expected_legacy) and legacy_recheck
    )
    paired = benchmark["benchmark"]
    if len(paired) != settings["runs"]:
        raise ValueError("Missing a predeclared benchmark repetition")
    args.output.mkdir()

    def document(name, data):
        (args.output / f"{name}.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def table(name, rows):
        columns = list(dict.fromkeys(key for row in rows for key in row))
        with (args.output / f"{name}.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, columns, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)

    comparisons, gates, deferrals = [], [], []
    for row in paired:
        index = row["repetition"]
        baseline = row["results"]["disabled"]["p95"]
        candidate = row["results"]["phase8d"]
        gates.append({"repetition": index, "disabled_p95_ms": round(baseline, 4),
                      "phase8c_p95_ms": round(row["results"]["phase8c"]["p95"], 4),
                      "phase8d_p95_ms": round(candidate["p95"], 4), "added_ms": round(row["added"], 4),
                      "max_ms": metric(candidate["deterministic"], "persistenceMs", 1), "passed": row["passed"]})
        for mode, values in row["results"].items():
            deterministic, shadow = values["deterministic"], values["shadow"]
            comparisons.append({"repetition": index, "mode": mode, "p95_ms": round(values["p95"], 4),
                "added_ms": round(values["p95"] - baseline, 4), "deterministic_samples": len(deterministic),
                "shadow_attempts": len(shadow), "deterministic_lost": values["lost"], "critical_retained": values["critical"],
                "deterministic_busy": values["deterministicBusy"], "shadow_busy": values["shadowBusy"],
                "shadow_success": values["finished"], "shadow_failed": values["failed"], "shadow_deferred": values["deferred"],
                **{f"{kind}_{field}_{quantile}": metric(samples, field, fraction)
                    for kind, samples, fields in (("det", deterministic, ("analysisMs", "persistenceMs", "waitMs", "heldMs", "overrunMs")),
                                                  ("shadow", shadow, ("waitMs", "heldMs", "injectedMs", "sleptMs", "outsideMs", "lateMs", "durationMs")))
                    for field in fields for quantile, fraction in (("median", .5), ("p95", .95), ("max", 1))}})
            if mode == "phase8d":
                for attempt, data in enumerate(shadow, 1):
                    deferrals.append({"repetition": index, "attempt": attempt, **data})
    table("writer_coordination_comparison", comparisons)
    table("sqlite_concurrency_repetitions", gates)
    table("monitoring_isolation_evaluation", [{key: row[key] for key in ("repetition", "mode", "deterministic_samples",
          "deterministic_lost", "critical_retained", "deterministic_busy", "det_overrunMs_p95", "det_overrunMs_max")} for row in comparisons])
    table("shadow_deferral_evaluation", deferrals)
    reconciliation = admissions["reconciliation"]
    table("admission_failure_evaluation", [
        {"fixture": "stalled_hint", **benchmark["coordinator"]},
        {"fixture": "verified_real_replay", "code": reconciliation["result"]["code"],
         "recorded_while_active": reconciliation["result"]["recorded"], "refusedMs": reconciliation["refusedMs"],
         "checkpointUnchanged": reconciliation["checkpointUnchangedOnRefusal"],
         "deterministicSamplePersisted": reconciliation["deterministicSamplePersisted"],
         "criticalFindingPersisted": reconciliation["criticalFindingPersisted"], "retry_ok": reconciliation["retry"]["ok"],
         "repeated": reconciliation["repeated"]["repeated"], "repeat_openings": reconciliation["repeated"]["openings"],
         "repeat_eligible": reconciliation["repeated"]["hypotheticalInterruptions"]},
        {"fixture": "already_admitted_writer", **residual},
    ])
    table("collector_restart_evaluation", collector["interruptions"])
    document("collector_outage_evaluation", collector["outage"])
    replay = ownership["realReplay"]
    table("notification_ownership_evaluation", [{key: item.get(key) for key in
          ("trigger", "rows", "repeated", "rawTransitions", "savedTransitions", "openings", "rotated", "hypotheticalInterruptions")}
          for item in replay["results"]])
    all_passed = all(row["passed"] for row in gates)
    preserved = all(row["deterministic_lost"] == 0 and row["deterministic_samples"] == row["critical_retained"]
                    for row in comparisons)
    logical = {"latency_gate": all_passed, "deterministic_retention": preserved, "operational_regression_passed": operational_passed,
               "collector_restart_points": len(collector["interruptions"]),
               "collector_outage_recovered_without_restart": collector["outage"]["sameCollector"],
               "outage_eligible": collector["outage"]["eligible"],
               "logical_replay": replay["metrics"],
               "exact_replay": operational["exactReplay"]["digest"],
               "refusal_checkpoint_unchanged": reconciliation["checkpointUnchangedOnRefusal"],
               "repeat_eligible": reconciliation["repeated"]["hypotheticalInterruptions"],
               "actual_notifications": 0}
    go = (all_passed and preserved and operational_passed and logical["collector_restart_points"] == 6
          and logical["collector_outage_recovered_without_restart"] and logical["outage_eligible"] == 0
          and logical["refusal_checkpoint_unchanged"] and logical["repeat_eligible"] == 0
          and replay["metrics"]["duplicateRejected"] == 0)
    document("acceptance_gates", {"decision": "GO TO PHASE 9 MLOPS LIFECYCLE" if go else "REPEAT PHASE 8",
             **logical, "operational_failed_tests": failed_tests, "legacy_collector_recheck_passed": legacy_recheck,
             "release_blocker_open": True})
    document("reproducibility_manifest", {
        "platform": platform.platform(), "python": platform.python_version(),
        "config_sha256": hashlib.sha256(config.read_bytes()).hexdigest(),
        "logical_fixture_sha256": operational["exactReplay"]["digest"],
        "decision_sha256": hashlib.sha256(json.dumps(logical, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "output_sha256": {file.name: hashlib.sha256(file.read_bytes()).hexdigest() for file in sorted(args.output.iterdir())}
    })


if __name__ == "__main__":
    main()
