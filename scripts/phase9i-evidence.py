"""Summarize saved Phase 9-I measurements without replacing any test evidence."""

import argparse
import csv
from hashlib import sha256
import json
from math import ceil, isclose
from pathlib import Path
import re
import runpy


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "docs/phase9i-reference/phase9i_config.json"


def read(file):
    return file.read_text(encoding="utf-8-sig")


def checksum(file):
    return sha256(file.read_bytes()).hexdigest()


def events(file, prefix):
    pieces = iter(read(file).splitlines())
    for line in pieces:
        if not line.startswith(prefix):
            continue
        payload = line.removeprefix(prefix)
        while True:
            try:
                item = json.loads(payload)
                break
            except json.JSONDecodeError:
                chunk = next(pieces, "")
                if not chunk.startswith("# ") or chunk.startswith("# Subtest:"):
                    raise ValueError(f"Incomplete diagnostic event in {file.name}") from None
                payload += chunk[2:]
        yield item


def row_for(file, prefix, kind, value):
    return next(item[value] for item in events(file, prefix) if item["kind"] == kind)


def percentile(values, level):
    return sorted(values)[ceil(level * len(values)) - 1] if values else None


def stat(values, name):
    return {f"{name}_p50_ms": percentile(values, .50), f"{name}_p95_ms": percentile(values, .95),
            f"{name}_max_ms": max(values, default=None)}


def take(rows, key):
    return [row[key] for row in rows if row.get(key) is not None]


def legacy(file, name, expected, config):
    delta = row_for(file, "# PHASE8 ", "latency", "data")
    totals = {row["data"]["mode"]: row["data"] for row in events(file, "# PHASE8 ") if row["kind"] == "isolation"}
    assert set(totals) == {"disabled", "shadow"}
    assert delta["threshold"] == config["gate_ms"]
    rows = []
    for mode, total in totals.items():
        deterministic = [entry["ms"] for entry in delta["timings"] if entry["mode"] == mode and entry["stage"] == "deterministic"]
        assert len(deterministic) == total["samples"] == config["legacy_mode_count"]
        shadow = [entry["ms"] for entry in delta["timings"] if entry["mode"] == mode and entry["stage"] == "shadow_export_and_write"]
        rows.append({"run": name, "mode": mode, **stat(deterministic, "analyze_persist_issue_read"), **stat(shadow, "shadow_export_and_write"),
                     "samples": len(deterministic), "lost_samples": total["dropped"], "critical_preserved": total["critical"],
                     "shadow_busy": total["busy"]})
    measured = rows[1]["analyze_persist_issue_read_p95_ms"] - rows[0]["analyze_persist_issue_read_p95_ms"]
    assert [row["mode"] for row in rows] == ["disabled", "shadow"] and isclose(measured, delta["delta"], abs_tol=1e-8)
    return rows, {"run": name, "result": expected, "added_p95_ms": measured, "threshold_ms": delta["threshold"]}


def paired(file, config):
    result = row_for(file, "# PHASE8D ", "benchmark", "value")
    assert [row["repetition"] for row in result] == list(range(1, config["paired_repetitions"] + 1))
    modes, comparisons = [], []
    for pair in result:
        for mode, data in pair["results"].items():
            deterministic, shadow = data["deterministic"], data["shadow"]
            assert len(deterministic) == 100 and len(shadow) == (0 if mode == "disabled" else 100)
            row = {"repetition": pair["repetition"], "mode": mode, "samples": len(deterministic),
                   "lost_samples": data["lost"], "critical_preserved": data["critical"],
                   "deterministic_busy": data["deterministicBusy"], "shadow_attempts": len(shadow),
                   "shadow_ok": data["finished"], "shadow_busy": data["shadowBusy"],
                   "shadow_admission_waited": data["deferred"],
                   "shadow_deferred_without_write": sum(item["outcome"] == "deferred" for item in shadow)}
            for items, keys in ((deterministic, {"persistenceMs": "persist", "waitMs": "writer_acquisition", "heldMs": "writer_hold",
                                                "overrunMs": "deterministic_overrun"}),
                                (shadow, {"waitMs": "shadow_acquisition", "heldMs": "shadow_hold", "lateMs": "shadow_schedule_delay",
                                          "deferredMs": "shadow_admission", "durationMs": "shadow_attempt", "sleptMs": "injected_hold"})):
                for key, label in keys.items():
                    row.update(stat(take(items, key), label))
            assert isclose(row["persist_p95_ms"], data["p95"], abs_tol=1e-8)
            modes.append(row)
        comparisons.append({"repetition": pair["repetition"],
                            "disabled_persist_p95_ms": pair["results"]["disabled"]["p95"],
                            "phase8c_persist_p95_ms": pair["results"]["phase8c"]["p95"],
                            "phase8d_persist_p95_ms": pair["results"]["phase8d"]["p95"],
                            "phase8d_added_p95_ms": pair["added"], "threshold_ms": config["gate_ms"],
                            "phase8d_result": "PASS" if pair["passed"] else "FAIL"})
    return modes, comparisons


def table(path, rows):
    with path.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, rows[0].keys(), lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: round(value, 6) if isinstance(value, float) else value for key, value in row.items()})


def document(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")


def export(input_dir, output, validation):
    config = json.loads(read(CONFIG))
    environment = json.loads(read(input_dir / "environment.json"))
    assert environment["configuration_sha256"] == checksum(CONFIG) and environment["competing_validation_processes_at_start"] == 0
    original = input_dir / "original-concurrent.tap"
    assert checksum(original) == "5ef6934b628315f38bbc8cf6a22381dafa6efbc2e92596c76c85ed1dc7a14007"
    raw, old = legacy(original, "original-concurrent", "FAIL", config)
    assert isclose(old["added_p95_ms"], 30.9497, abs_tol=1e-8)
    assert "# fail 1" in read(original).splitlines()
    measures = input_dir / "measurements"
    execution = {entry["label"]: entry for entry in map(json.loads, read(measures / "execution.jsonl").splitlines())}
    legacy_runs = [old]
    for group, count in (("isolated", config["isolated_repetitions"]), ("stress", config["stress_repetitions"])):
        for index in range(1, count + 1):
            label = f"{group}-{index}"
            exit_code = execution[label]["exitCode"]
            modes, summary = legacy(measures / f"{label}.tap", label, "PASS" if exit_code == 0 else "FAIL", config)
            assert (exit_code == 0) == (summary["added_p95_ms"] <= config["gate_ms"])
            legacy_runs.append(summary)
            raw.extend(modes)
    modes, comparisons = paired(measures / "phase8d-paired.tap", config)
    assert execution["phase8d-paired"]["exitCode"] == 0
    historical = runpy.run_path(str(ROOT / "scripts/phase9h-evidence.py"))["frozen"]()
    phase9h = ROOT / "docs/phase9h-reference"
    http_manifest = json.loads(read(phase9h / "reproducibility_manifest.json"))
    assert all(checksum(phase9h / filename) == value for filename, value in http_manifest["output_sha256"].items())
    assert checksum(phase9h / "phase9h_config.json") == http_manifest["config_sha256"]
    if http_manifest["logical_decision_sha256"] != "1bee64317e702be5d9557eac6d39322143e20a6c985bcd41277708bc282b69ba":
        raise ValueError("Unexpected frozen Phase 9-H decision")
    reconciliation = row_for(input_dir / "admission.tap", "# PHASE8D ", "reconciliation", "value")
    residual = row_for(input_dir / "admission.tap", "# PHASE8D ", "residual", "value")
    coordinator = row_for(input_dir / "coordinator.tap", "# PHASE8D ", "coordinator", "value")
    owner = next(events(input_dir / "ownership.tap", "# PHASE9_OWNERSHIP "))
    phase9 = json.loads(read(ROOT / "docs/phase9-reference/results/reproducibility_manifest.json"))
    http_runs = list(events(input_dir / "http.tap", "# PHASE9H "))
    http = {"repetitions": len(http_runs), "legacy_resets": sum(
        row["id"] == "above_cap_raw_full_upload" and row["error"] == "ECONNRESET" for run in http_runs for row in run["cases"]),
        "corrected_complete_413": sum(
            row["id"] == "above_cap_raw_header_probe" and row["status"] == 413 and row["complete"] for run in http_runs for row in run["cases"]),
        "recovery_pass": sum(row.get("recovery") is True for run in http_runs for row in run["cases"]),
        "actual_notifications": sum(run["actualNotifications"] for run in http_runs)}
    admission = {"bounded_deferral_ms": coordinator["elapsedMs"], "coordinator_recovery_ms": coordinator["recoveryMs"],
                 "checkpoint_unchanged_on_deferral": reconciliation["checkpointUnchangedOnRefusal"],
                 "false_success_recorded": reconciliation["result"]["recorded"],
                 "retry_succeeded": reconciliation["retry"]["ok"], "replayed_duplicate_openings": reconciliation["repeated"]["openings"],
                 "replayed_duplicate_hypothetical_openings": reconciliation["repeated"]["hypotheticalInterruptions"],
                 "already_admitted_writer_wait_ms": residual["deterministicWaitMs"],
                 "residual_sample_preserved": residual["deterministicSamplePersisted"],
                 "residual_critical_preserved": residual["criticalFindingPersisted"]}
    isolated = [row for row in legacy_runs if row["run"].startswith("isolated-")]
    verified = all(row["result"] == "PASS" for row in isolated) and all(validation.values()) and (
        all(row["lost_samples"] == 0 and row["samples"] == row["critical_preserved"] for row in raw + modes)
        and admission["checkpoint_unchanged_on_deferral"] and not admission["false_success_recorded"] and admission["retry_succeeded"]
        and owner["digest"] == phase9["ownership_decision_sha256"]
        and owner["duplicateLogicalOpenings"] == owner["duplicateHypotheticalOpenings"] == owner["actualNotifications"] == 0
        and admission["replayed_duplicate_openings"] == admission["replayed_duplicate_hypothetical_openings"] == 0
        and (http["repetitions"], http["corrected_complete_413"], http["recovery_pass"], http["actual_notifications"])
        == (5, 5, 20, 0))
    statuses = {"decision": "SQLITE ISOLATED GATE VERIFIED" if verified else "REPEAT SQLITE INVESTIGATION",
                "original": old, "isolated_passes": sum(row["result"] == "PASS" for row in isolated),
                "phase8d_comparison_passes": sum(row["phase8d_result"] == "PASS" for row in comparisons),
                "frozen_counts": historical, "phase9h_output_hashes": len(http_manifest["output_sha256"]),
                "validation": validation, "ownership_digest": owner["digest"], "http": http}
    output.mkdir()
    table(output / "legacy_run_results.csv", legacy_runs)
    table(output / "legacy_mode_metrics.csv", raw)
    table(output / "paired_mode_metrics.csv", modes)
    table(output / "paired_comparisons.csv", comparisons)
    document(output / "environment.json", environment)
    document(output / "execution.json", execution)
    document(output / "admission_and_recovery.json", admission)
    document(output / "acceptance_gates.json", statuses)
    archived = [original, *sorted(measures.glob("*.tap")), *sorted(input_dir.glob("*.tap"))]
    document(output / "reproducibility_manifest.json", {
        "configuration_sha256": checksum(CONFIG),
        "archived_tap_sha256": {file.name: checksum(file) for file in archived},
        "output_sha256": {file.name: checksum(file) for file in sorted(output.iterdir())},
    })
    print(statuses["decision"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("validation", type=Path)
    args = parser.parse_args()
    export(args.archive, args.output, json.loads(read(args.validation)))
