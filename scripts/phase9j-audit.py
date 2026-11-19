"""Read source and saved measurements to produce a new offline architecture audit."""

import argparse
import csv
from hashlib import sha256
import json
from pathlib import Path
import runpy
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SOURCES = {
    "scripts/health-worker.cjs": ["function heartbeat(", "markDeterministic(activity, true)", "store.saveSample(sample)", "shadow?.submit(sample.id)"],
    "lib/health/core.cjs": ["function createStore(", "PRAGMA journal_mode=WAL", "function saveSample(", "clear()"],
    "lib/health/shadow/scheduler.cjs": ["function scheduler(", "submit(id)", "function createShadowScheduler("],
    "lib/health/shadow/thread.cjs": ["new DatabaseSync(workerData.path)", "PRAGMA busy_timeout=50", "parentPort.on("],
    "lib/health/shadow/job.cjs": ["function createJob(", "await client.infer(", "await admission.wait()", "const counts = consume("],
    "lib/health/shadow/admission.cjs": ["const MAX_DEFERRAL_MS", "const RETRY_MS", "async wait()"],
    "lib/health/shadow/store.cjs": ["function transaction(", "function migrateObservability(", "function consume(", "sampleExists.get(", "function clearShadow("],
    "lib/health/shadow/export.cjs": ["function exportReplay(", "const replayId = hash(", "const history = db.prepare("],
    "lib/health/shadow/observe.cjs": ["function readShadow(", "readOnly: true", "const anchorExists", "function handleShadow("],
    "lib/health/shadow/eligibility.cjs": ["const NOTIFICATION_POLICY", "function recordOpening("],
    "lib/health/shadow/client.cjs": ["function createClient(", "agent: false"],
    "app/api/monitor/route.ts": ['body.action === "start"', 'body.action === "clear"', "store.saveAnalysis("],
    "app/api/monitor/shadow/route.ts": ["export function GET("],
    "ml/src/ultron_health_ml/service/schema.py": ["MAX_ROWS =", "MAX_HISTORY_SECONDS =", "MAX_BODY_BYTES ="],
    "ml/src/ultron_health_ml/governance/registry.py": ["APPROVED_IDENTITY =", "ARTIFACTS =", '"ledger":', "def verify("],
    "scripts/phase8-shadow.test.cjs": ["equivalent deterministic workloads", "const p95 = mode"],
    "scripts/phase8d.test.cjs": ["async function workload(", "five paired repetitions"],
    "scripts/phase8d-timing.cjs": ["function traceTransactions("],
}


def read(path):
    return path.read_text(encoding="utf-8-sig")


def load(path):
    return json.loads(read(path))


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def export(args):
    base = "e219197b4494b125c0c15df301af0466b4bd1da9"
    if subprocess.check_output(("git", "rev-parse", "HEAD"), cwd=ROOT, text=True).strip() != base:
        raise ValueError("Unexpected audit baseline")
    counts = runpy.run_path(str(ROOT / "scripts/phase9h-evidence.py"))["frozen"]()
    integrity = []
    for phase in ("phase9h", "phase9i"):
        directory = ROOT / "docs" / (phase + "-reference")
        manifest = load(directory / "reproducibility_manifest.json")
        for name, expected in manifest["output_sha256"].items():
            if digest(directory / name) != expected:
                raise ValueError(f"Frozen output mismatch: {phase}/{name}")
        configuration = "phase9h_config.json" if phase == "phase9h" else "phase9i_config.json"
        expected = manifest["config_sha256" if phase == "phase9h" else "configuration_sha256"]
        if digest(directory / configuration) != expected:
            raise ValueError("Frozen configuration mismatch")
        integrity.append({"phase": phase, "output_hashes": len(manifest["output_sha256"]), "configuration": "MATCH"})
    frozen_i = load(ROOT / "docs/phase9i-reference/reproducibility_manifest.json")
    for name, expected in frozen_i["archived_tap_sha256"].items():
        path = args.research_archive / name
        if not path.exists():
            path = args.research_archive / "measurements" / name
        if digest(path) != expected:
            raise ValueError(f"Research TAP changed: {name}")
    original_h = load(ROOT / "docs/phase9h-reference/original_failure_reference.json")
    frozen_h = load(ROOT / "docs/phase9h-reference/reproducibility_manifest.json")
    for name, expected in (("original-regressions.tap", original_h["original_tap_sha256"]),
                           ("phase9h.tap", frozen_h["matrix_tap_sha256"])):
        if digest(args.http_archive / name) != expected:
            raise ValueError("Original HTTP evidence changed")
    helper = runpy.run_path(str(ROOT / "scripts/phase9i-evidence.py"))
    config = load(ROOT / "docs/phase9i-reference/phase9i_config.json")
    paired_path = args.integration_archive / "measurements/phase8d-paired.tap"
    modes, pairs = helper["paired"](paired_path, config)
    if len(pairs) != 5 or [row["phase8d_result"] for row in pairs] != ["FAIL", "PASS", "PASS", "PASS", "PASS"]:
        raise ValueError("Unexpected original integration observations")
    failed = pairs[0]
    if abs(failed["phase8d_added_p95_ms"] - 30.8624) > 1e-6:
        raise ValueError("Integration failure has changed")
    if digest(paired_path) != "d166f53bcc0fc050ee3f84cd21be1faca39203c6ef36f20e43b9483d63f01863":
        raise ValueError("Integration raw TAP has changed")
    source = []
    for name, markers in SOURCES.items():
        lines = read(ROOT / name).splitlines()
        for marker in markers:
            number = next((index for index, line in enumerate(lines, 1) if marker in line), None)
            if number is None:
                raise ValueError(f"Source marker missing: {name}: {marker}")
            source.append({"path": name, "line": number, "marker": marker, "sha256": digest(ROOT / name)})
    validation = load(args.validation)
    for filename, expected in (("replay.tap", 12), ("admission.tap", 5), ("coordinator.tap", 1),
                               ("ownership.tap", 1), ("atomicity.tap", 2), ("http.tap", 1)):
        lines = read(args.validation.parent / filename).splitlines()
        passed = f"# pass {expected}" in lines and "# fail 0" in lines and "# skipped 0" in lines
        if validation[filename] != passed:
            raise ValueError(f"Incorrect regression assertion for {filename}")
    output = args.output
    output.mkdir()

    def document(name, data):
        with (output / name).open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, indent=2)
            stream.write("\n")

    def table(name, rows):
        with (output / name).open("x", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, list(rows[0]))
            writer.writeheader()
            for row in rows:
                writer.writerow({key: round(value, 6) if isinstance(value, float) else value for key, value in row.items()})

    table("source_inventory.csv", source)
    table("integration_paired_results.csv", pairs)
    table("integration_writer_metrics.csv", modes)
    document("frozen_integrity.json", {"historical": counts, "additional": integrity,
              "phase9i_archived_tap_hashes": 15, "http_original_and_matrix_tap": "MATCH",
              "original_legacy_failure": "30.9497 ms FAIL preserved",
              "integration_failure": "30.8624 ms FAIL preserved"})
    document("historical_failure_reference.json", {
        "phase9i_integration_raw_tap_sha256": digest(paired_path),
        "phase9i_integration_verification_status": "1 of 5 Phase 8-D pairs failed; harness exit code was zero",
        "legacy_failure_raw_tap_sha256": frozen_i["archived_tap_sha256"]["original-concurrent.tap"],
        "raw_archive": "Retained locally outside Git; sanitized summaries only in published evidence",
        "original_legacy_root_cause": "Unproven; legacy single-threaded fixture and paired independent-writer fixture differ",
        "failed_pair": failed,
        "same_pair_writer_metrics": [row for row in modes if row["repetition"] == 1],
        "measurement_limits": "Acquisition includes OS scheduling and disk wait; no per-operation I/O traces or causal packet/host timeline. These are saved measurements, not new Phase 9-J timings.",
    })
    document("validation_results.json", validation)
    fingerprint = {file.name: digest(file) for file in sorted(output.iterdir())}
    document("reproducibility_manifest.json", {
        "baseline": base, "assessment_only": True, "new_architecture_implemented": False,
        "source_sha256": {name: digest(ROOT / name) for name in sorted(SOURCES)},
        "assessment_sha256": {name: digest(ROOT / "docs/phase9j-reference" / name) for name in
                              ("assessment_scope.json", "option_comparison.json", "proposed_stages_and_gates.json")},
        "report_sha256": digest(ROOT / "docs/PHASE-9-J-WRITER-ARCHITECTURE.md"),
        "audit_script_sha256": digest(Path(__file__)),
        "new_regression_tap_sha256": {name: digest(args.validation.parent / name) for name in validation if name.endswith(".tap")},
        "output_sha256": fingerprint,
    })
    print("RECOMMEND ARCHITECTURAL CHANGE")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--research-archive", type=Path, required=True)
    parser.add_argument("--integration-archive", type=Path, required=True)
    parser.add_argument("--http-archive", type=Path, required=True)
    parser.add_argument("--validation", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    export(parser.parse_args())
