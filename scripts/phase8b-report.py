"""Publish isolated Phase 8-B TAP measurements without overwriting prior evidence."""

import argparse
import csv
import hashlib
import json
import math
import platform
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operational_tap", type=Path)
    parser.add_argument("replay_tap", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--prior-operational", type=Path)
    args = parser.parse_args()
    operational = args.operational_tap.read_text(encoding="utf-8-sig")
    replay_log = args.replay_tap.read_text(encoding="utf-8-sig")
    if "# fail 0" not in operational or "# fail 0" not in replay_log:
        raise SystemExit("Use successful TAP runs for both inputs")
    parsed = {}
    for line in operational.splitlines():
        if line.startswith("# PHASE8B "):
            row = json.loads(line.removeprefix("# PHASE8B "))
            parsed[row["key"]] = row["value"]
    replay = next(json.loads(line.removeprefix("# PHASE8 "))["data"] for line in replay_log.splitlines()
                  if line.startswith('# PHASE8 {"kind":"realReplay"'))
    args.output.mkdir()

    def document(name, data):
        (args.output / f"{name}.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def table(name, rows):
        columns = list(dict.fromkeys(key for row in rows for key in row))
        with (args.output / f"{name}.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, columns, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)

    def latency(values, name):
        ordered = sorted(values)
        return {f"{name}_n": len(ordered), **{
            f"{name}_{label}_ms": round(ordered[math.ceil(len(ordered) * quantile) - 1], 4) if ordered else None
            for label, quantile in (("median", .5), ("p95", .95), ("max", 1))
        }}

    concurrent_runs = [("final", parsed["concurrent"])]
    if args.prior_operational:
        prior = args.prior_operational.read_text(encoding="utf-8-sig")
        if "# fail 0" not in prior:
            raise SystemExit("Use a successful earlier operational TAP run")
        measured = next(json.loads(line.removeprefix("# PHASE8B "))["value"] for line in prior.splitlines()
                        if line.startswith('# PHASE8B {"key":"concurrent"'))
        concurrent_runs.insert(0, ("initial", measured))
    isolation = []
    for label, concurrent in concurrent_runs:
        for row in concurrent["results"]:
            shadow = row["shadow"]
            isolation.append({"run": label, "mode": row["mode"], "samples": len(row["samples"]), "lost": row["lost"],
                              "critical": row["critical"], "shadow_busy": shadow["busy"], "shadow_success": shadow["ok"],
                              "p95_delta_ms": concurrent["delta"], "p95_gate": concurrent["gate"],
                              **latency([sample["analysis"] for sample in row["samples"]], "analysis"),
                              **latency([sample["persistence"] for sample in row["samples"]], "persist"),
                              **latency(row["overruns"], "overrun"), **latency(shadow["durations"], "shadow")})
    document("collector_lifecycle_evaluation", parsed["collector"])
    table("collector_restart_evaluation", parsed["collector"]["rows"])
    table("outage_recovery_evaluation", parsed["outage"])
    table("http_body_boundary_evaluation", parsed["body"])
    table("sqlite_concurrent_writer_evaluation", isolation)
    table("monitoring_isolation_evaluation", isolation)
    table("replay_rebase_evaluation", [{key: row.get(key) for key in ("trigger", "rows", "repeat", "rotated", "repeated")}
                                      for row in replay["results"]])
    table("notification_eligibility_evaluation", [{key: row.get(key) for key in
          ("trigger", "openings", "rotated", "hypotheticalInterruptions")} for row in replay["results"]])
    metrics = replay["metrics"]
    table("duplicate_notice_evaluation", [{"requests": len(replay["results"]), "raw": metrics["raw"],
          "validated": metrics["validated"], "repeated": metrics["repeated"], "persisted": metrics["persisted"],
          "eligible": metrics["eligibility"]["eligible"], "duplicate_open_rejections": metrics["duplicateRejected"],
          "actual_notifications": 0}])
    prior = Path(__file__).resolve().parents[1] / "docs/phase8-reference/results/reproducibility_manifest.json"
    digest = json.loads(prior.read_text())["decision_digest_sha256"]
    document("shadow_reliability_results", {"platform": platform.platform(), "python": platform.python_version(),
             "collector": parsed["collector"], "outage": parsed["outage"], "replay": metrics, "concurrent": dict(concurrent_runs),
             "exact_replay": parsed["exactReplay"], "actual_notifications": 0})
    document("acceptance_gates", {"decision": "REPEAT PHASE 8", "concurrent_p95_pass": all(run["gate"] for _, run in concurrent_runs),
             "collector": "partial", "outage": "real scheduler with virtual clock; no combined extended collector outage",
             "body": "exact cap verified with legal JSON whitespace, not natural payload growth",
             "ownership": "exact identities, transitions and eligibility match for two fixture databases with the same test installation and history"})
    document("reproducibility_manifest", {"phase8b_exact_decision_digest_sha256": parsed["exactReplay"]["digest"],
             "phase8a_decision_digest_sha256": digest,
             "output_sha256": {file.name: hashlib.sha256(file.read_bytes()).hexdigest() for file in sorted(args.output.iterdir())}})


if __name__ == "__main__":
    main()
