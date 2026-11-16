"""Extract Phase 8 evidence from a successful TAP run into a new output directory."""

import argparse
import csv
import hashlib
import json
import math
import platform
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("tap", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    lines = args.tap.read_text(encoding="utf-8-sig").splitlines()
    if "# fail 0" not in lines or "# pass 13" not in lines:
        raise SystemExit("Expected successful combined Phase 7 and Phase 8 TAP run")
    records = [json.loads(line.removeprefix("# PHASE8 ")) for line in lines if line.startswith("# PHASE8 ")]
    evidence = {record["kind"]: record["data"] for record in records if record["kind"] != "isolation"}
    isolation = [record["data"] for record in records if record["kind"] == "isolation"]
    replay, actual, latency = (evidence[key] for key in ("replay", "realReplay", "latency"))
    args.output.mkdir()

    def write_json(name, data):
        (args.output / f"{name}.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def write_csv(name, rows):
        with (args.output / f"{name}.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)

    replay_summary = {key: value for key, value in replay.items() if not isinstance(value, list)}
    stable = {
        "store_replay": replay_summary,
        "real_replay": actual["metrics"],
        "isolation": isolation,
        "windows": replay["windows"],
        "restart_checkpoints": replay["restarts"],
        "notification_simulation": replay["interruptions"],
    }
    write_json("shadow_reliability_results", {**stable, "environment": platform.system() + " " + platform.machine(),
                                            "added_deterministic_p95_ms": latency["delta"]})
    write_csv("replay_rotation_evaluation", replay["windows"])
    write_csv("restart_recovery_evaluation", replay["restarts"])
    write_csv("sqlite_contention_evaluation", isolation)
    write_csv("monitoring_isolation_evaluation", isolation)
    write_csv("notification_eligibility_evaluation", replay["interruptions"])
    write_csv("duplicate_notice_evaluation", [
        {"scope": "store_fixture", "raw": replay["raw"], "validated": "", "repeated": replay["repeated"],
         "persisted": replay["persisted"], "eligible": replay["eligible"], "duplicate_openings": replay["duplicateLogicalOpenings"]},
        {"scope": "real_http", "raw": actual["metrics"]["raw"], "validated": actual["metrics"]["validated"],
         "repeated": actual["metrics"]["repeated"], "persisted": actual["metrics"]["persisted"],
         "eligible": actual["metrics"]["eligibility"]["eligible"], "duplicate_openings": actual["metrics"]["duplicateRejected"]},
    ])
    groups = {"read_only_handler": evidence["readOnlyHandler"]}
    for row in latency["timings"]:
        groups.setdefault(f'{row["mode"]}_{row["stage"]}', []).append(row["ms"])
    for row in actual["results"]:
        size = "large" if row["rows"] >= 900 else "small"
        for stage, milliseconds in row["timing"].items():
            groups.setdefault(f"http_replay_{size}_{stage}", []).append(milliseconds)
            groups.setdefault(f"http_replay_all_{stage}", []).append(milliseconds)
    write_csv("shadow_latency_evaluation", [
        {"stage": stage, "samples": len(values), "median_ms": round(sorted(values)[math.ceil(len(values) / 2) - 1], 4),
         "p95_ms": round(sorted(values)[math.ceil(len(values) * .95) - 1], 4) if len(values) >= 20 else ""}
        for stage, values in groups.items()
    ])
    canonical = json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()
    write_json("reproducibility_manifest", {
        "decision_digest_sha256": hashlib.sha256(canonical).hexdigest(),
        "digest_excludes": ["elapsed timings", "absolute timestamps", "ephemeral identities"],
        "output_sha256": {file.name: hashlib.sha256(file.read_bytes()).hexdigest() for file in sorted(args.output.iterdir())},
    })


if __name__ == "__main__":
    main()
