"""Export Phase 8-C measurements, retaining every predeclared benchmark run."""

import argparse
import csv
import hashlib
import json
import math
import platform
from pathlib import Path


def extract(path, prefix, key):
    entries = (json.loads(line.removeprefix(prefix)) for line in path.read_text(encoding="utf-8-sig").splitlines()
               if line.startswith(prefix))
    return {entry[key]: entry.get("value", entry.get("data")) for entry in entries}


def percentile(values, fraction):
    return round(sorted(values)[math.ceil(len(values) * fraction) - 1], 4) if values else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("benchmarks", type=Path)
    parser.add_argument("logs", type=Path)
    parser.add_argument("output", type=Path)
    arguments = parser.parse_args()
    reference = Path(__file__).resolve().parents[1] / "docs/phase8c-reference"
    config = json.loads((reference / "phase8c_config.json").read_text())
    def log(name, prefix="# PHASE8C ", key="kind"):
        return extract(arguments.logs / f"ultron-8c-{name}.tap", prefix, key)

    collector = log("collector")
    write = log("write")
    write_path = write["writePath"]
    replay = log("replay", "# PHASE8 ")["realReplay"]
    operational = log("operational", "# PHASE8B ", "key")
    count = config["independent_acceptance_runs"]
    benchmarks = [extract(arguments.benchmarks / f"run-{index}.tap", "# PHASE8B ", "key")["concurrent"]
                  for index in range(1, count + 1)]
    arguments.output.mkdir()

    def write_json(name, data):
        (arguments.output / f"{name}.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def write_table(name, data):
        columns = list(dict.fromkeys(key for row in data for key in row))
        with (arguments.output / f"{name}.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, columns, lineterminator="\n")
            writer.writeheader()
            writer.writerows(data)

    results, isolation = [], []
    for index, measured in enumerate(benchmarks, 1):
        disabled, candidate = measured["results"]
        results.append({
            "run": index, "added_p95_ms": round(measured["delta"], 4), "passed": measured["gate"],
            "disabled_p95_ms": percentile([row["persistence"] for row in disabled["samples"]], .95),
            "shadow_p95_ms": percentile([row["persistence"] for row in candidate["samples"]], .95),
            "shadow_max_ms": percentile([row["persistence"] for row in candidate["samples"]], 1),
            "shadow_successes": candidate["shadow"]["ok"], "shadow_busy": candidate["shadow"]["busy"],
            "lost": candidate["lost"], "critical": candidate["critical"]
        })
        for row in (disabled, candidate):
            isolation.append({"run": index, "mode": row["mode"], "samples": len(row["samples"]),
                              "lost": row["lost"], "critical": row["critical"],
                              "overrun_p95_ms": percentile(row["overruns"], .95),
                              "overrun_max_ms": percentile(row["overruns"], 1)})
    write_table("sqlite_concurrency_repetitions", results)
    write_table("monitoring_isolation_evaluation", isolation)
    write_table("sqlite_write_path_comparison", [
        {"store": row["label"], "repeats": len(row["repeats"]),
         **{f"{metric}_{label}": percentile([item[metric] for item in row["repeats"]], quantile)
            for metric in ("heldMs", "elapsedMs") for label, quantile in (("median", .5), ("p95", .95), ("max", 1))}}
        for row in write_path
    ])
    interrupts = collector.get("interruptions", [{"result": "not validated"}])
    write_table("collector_interruption_evaluation", interrupts)
    write_table("collector_restart_evaluation", interrupts)
    outage = collector.get("outage")
    write_table("extended_outage_recovery", outage["rows"] if outage else [{"result": "not validated"}])
    write_table("replay_rebase_recovery", [
        {key: row.get(key) for key in ("trigger", "rows", "rotated", "openings", "hypotheticalInterruptions")}
        for row in replay["results"]
    ])
    write_table("notification_ownership_evaluation", [
        {"trigger": row["trigger"], "persisted": row["savedTransitions"], "repeated": row["repeated"],
         "eligible": row["hypotheticalInterruptions"]} for row in replay["results"]
    ])
    write_table("duplicate_notice_evaluation", [
        {"requests": len(replay["results"]), **{key: replay["metrics"][key]
         for key in ("raw", "validated", "repeated", "persisted", "duplicateRejected")},
         "eligible": replay["metrics"]["eligibility"]["eligible"], "actual_notifications": 0}
    ])
    write_table("http_boundary_regression", operational["body"])
    write_json("acceptance_gates", {
        "decision": "REPEAT PHASE 8",
        "sqlite_latency": all(row["passed"] for row in results),
        "failed_repetitions": [row["run"] for row in results if not row["passed"]],
        "collector_fault_points": len(collector.get("interruptions", [])),
        "collector_outage": outage,
        "independent_writer_busy_stress": write.get("busyStress"),
        "limitations": ["Full 12-hour and 1800-row acquisition progression were not driven inside the Windows collector.",
                        "Combined restart or Python outage under concurrent faulted writers was not validated."],
        "actual_notifications": 0
    })
    write_json("reproducibility_manifest", {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "exact_decision_digest_sha256": operational["exactReplay"]["digest"],
        "config_sha256": hashlib.sha256((reference / "phase8c_config.json").read_bytes()).hexdigest(),
        "output_sha256": {file.name: hashlib.sha256(file.read_bytes()).hexdigest() for file in sorted(arguments.output.iterdir())}
    })


if __name__ == "__main__":
    main()
