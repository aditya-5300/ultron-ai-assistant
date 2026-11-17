"""Generate Phase 9 evidence in a new directory, independent of the app and service."""

import argparse
import csv
from hashlib import sha256
import json
from pathlib import Path
import platform
import shutil
import tempfile
from time import perf_counter

from ultron_health_ml.governance.drift import evaluate, make_reference
from ultron_health_ml.governance.fixtures import SCENARIOS, SPLITS, fixture
from ultron_health_ml.governance.registry import (
    ARTIFACTS, GATES, Evaluation, Manifest, Registry, Rejected, Simulation, dependency_versions, digest, load_registry,
    read_json, verify, write_evaluation,
)

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "docs/phase9-reference"


def build_report(root, ownership, config):
    timings = []

    def timed(operation, action):
        start = perf_counter()
        try:
            return action()
        finally:
            timings.append({"operation": operation, "duration_ms": round((perf_counter() - start) * 1000, 4)})

    registry = timed("manifest_parse_schema", lambda: load_registry(SOURCE / "policy_registry.json"))
    baseline, candidate = registry.entries
    versions = timed("dependency_discovery", dependency_versions)
    compatibility = []
    integrity = []
    for manifest in registry.entries:
        identity = timed("artifact_checks_and_runtime_compatibility", lambda: verify(manifest, root, baseline, versions))
        compatibility.append({"case": manifest.version, "result": "PASS", "analytical_identity": identity})
        integrity.append({"case": manifest.version, "result": "PASS", "checked_artifacts": len(ARTIFACTS)})
    for field, value in (("telemetry_schema", "0"), ("detection_fingerprint", "0" * 64),
                         ("forecast_policy", "persistence:60:0.65"), ("forecasting", ["cpu"])):
        try:
            Manifest.model_validate({**candidate.model_dump(mode="json"), field: value})
        except ValueError:
            compatibility.append({"case": field, "result": "REJECTED", "analytical_identity": None})
        else:
            raise AssertionError("unsupported setting accepted")
    for case in ("missing", "modified"):
        with tempfile.TemporaryDirectory(prefix="ultron-p9-artifacts-") as directory:
            isolated = Path(directory)
            for name, relative in ARTIFACTS.items():
                output = isolated / relative
                output.parent.mkdir(parents=True, exist_ok=True)
                if name != "bundle":
                    shutil.copyfile(root / relative, output)
                elif case == "modified":
                    output.write_text("{}", encoding="utf-8")
            try:
                verify(candidate, isolated, baseline, versions)
            except Rejected as error:
                integrity.append({"case": case, "result": str(error), "checked_artifacts": 0})
            else:
                raise AssertionError("invalid artifact accepted")

    reference = make_reference(fixture("reference", "stable"), "reference", SPLITS["reference"])
    summaries, quality_rows, datasets = [], [], {"reference/stable": reference["dataset_hash"]}
    for split in ("development", "locked"):
        for scenario in SCENARIOS:
            window = fixture(split, scenario)
            datasets[f"{split}/{scenario}"] = digest(window.model_dump(mode="json"))
            result = timed("quality_and_drift", lambda: evaluate(window, reference, digest(reference)))
            summaries.append({"split": split, "scenario": scenario, "status": result["status"],
                              "context": "|".join(result["context_reasons"]),
                              "metrics": digest(result["metrics"])})
            for metric, row in result["quality"]["metrics"].items():
                quality_rows.append({"split": split, "scenario": scenario, "metric": metric, **row,
                    "metric_drift": result["metrics"][metric]["status"],
                    "metric_reasons": "|".join(result["metrics"][metric]["reasons"]),
                    "polling_gaps": result["quality"]["polling_gaps"],
                    "irregular_intervals": result["quality"]["irregular_intervals"],
                    "latest_segment_seconds": result["quality"]["latest_segment_seconds"],
                    "unsupported_measurements": "|".join(result["quality"]["unsupported_measurements"])})
    assert {row["scenario"]: row["status"] for row in summaries if row["split"] == "locked"} == config["expected"]
    assert ownership["runs"] == 2 and ownership["duplicateLogicalOpenings"] == ownership["duplicateHypotheticalOpenings"] == 0
    assert ownership["actualNotifications"] == 0
    rows_by_action = {row["action"]: row for row in ownership["rows"]}
    assert rows_by_action["ambiguous_identity"]["checkpointUnchanged"]
    assert rows_by_action["genuine_recurrence"]["logical"]["hypotheticalInterruptions"] > 0
    assert rows_by_action["namespace_rebase"]["logical"]["rotated"]
    prior = read_json(root / ARTIFACTS["phase8_evidence"])
    assert prior["deterministic_retention"] and prior["latency_gate"] and prior["actual_notifications"] == 0
    assert max(row["logical"]["rows"] for row in ownership["rows"] if "logical" in row) <= config["max_window_rows"]

    statuses = {gate: "PASS" for gate in GATES}
    metrics = {"locked-scenarios": len(config["expected"]), "duplicate-openings": 0, "actual-notifications": 0,
               "ownership-runs": ownership["runs"]}
    promotion, rollback, evaluations = [], [], []
    with tempfile.TemporaryDirectory(prefix="ultron-p9-journal-") as directory:
        journal = Path(directory)
        for outcome, evaluation_id in (("PASS", candidate.evaluation_reference), ("FAIL", "phase9-negative-eval"),
                                        ("INCONCLUSIVE", "phase9-inconclusive-eval")):
            evaluated_candidate = candidate.model_copy(update={"evaluation_reference": evaluation_id})
            evaluated_registry = Registry.model_validate({"schema_version": "1.0.0", "entries": [baseline, evaluated_candidate]})
            gates = {**statuses, "ownership": outcome}
            evidence = {"gates": gates, "input_hash": digest(datasets), "ownership_digest": ownership["digest"],
                        "prerequisites": "Fixture simulation; not approval to change production"}
            record = Evaluation.model_validate({
                "schema_version": "1.0.0", "evaluation_id": evaluation_id,
                "manifest_hash": evaluated_candidate.manifest_hash, "dataset_hash": digest(datasets),
                "configuration_hash": digest(config), "evidence_hash": digest(evidence),
                "telemetry_schema": "1.0.0", "feature_fingerprint": candidate.detection_fingerprint,
                "forecast_fingerprint": candidate.forecast_fingerprint, "seed": SPLITS["locked"],
                "python_version": platform.python_version(), "dependencies": versions,
                "gates": gates, "result": outcome, "metrics": metrics,
            })
            checksum = timed("immutable_evaluation_write", lambda: write_evaluation(journal, record))
            evaluations.append({"policy_id": candidate.policy_id, "policy_version": candidate.version,
                                "evaluation": record.model_dump(mode="json"), "record_sha256": checksum,
                                "evidence": evidence})
            simulation = Simulation(evaluated_registry, root, baseline)
            promotion.append({"case": outcome, **simulation.validate(record, evidence)})
            if outcome == "PASS":
                try:
                    simulation.promote()
                except Rejected as error:
                    promotion.append({"case": "approval_missing", "action": "simulate_promotion",
                                      "selected": simulation.selected.version, "state": simulation.state, "reason": str(error)})
                else:
                    raise AssertionError("promotion occurred without approval")
                promotion.append({"case": outcome, **simulation.promote(explicitly_approved=True)})
                for reason in ("INVALID_FINDINGS", "RESOURCE_LIMIT", "UNAVAILABLE", "INCOMPATIBLE"):
                    replay = Simulation(registry, root, baseline)
                    replay.validate(record, evidence)
                    replay.promote(explicitly_approved=True)
                    result = timed("rollback_simulation", lambda: replay.rollback(reason, explicitly_approved=True))
                    rollback.append(result)
                    assert result["selected"] == "1.0.0"
            else:
                assert simulation.selected.version == "1.0.0"

    return {"compatibility": compatibility, "integrity": integrity, "promotion": promotion, "rollback": rollback,
            "reference": reference, "quality": quality_rows, "drift": summaries, "evaluations": evaluations,
            "datasets": datasets, "ownership": ownership, "gates": statuses}, timings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("ownership_tap", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    lines = args.ownership_tap.read_text(encoding="utf-8-sig").splitlines()
    prefix = "# PHASE9_OWNERSHIP "
    if "# fail 0" not in lines or "# pass 1" not in lines:
        raise SystemExit("Provide a successful Phase 9 ownership TAP file")
    ownership = next(json.loads(line[len(prefix):]) for line in lines if line.startswith(prefix))
    config = read_json(SOURCE / "phase9_config.json")
    first, timings = build_report(REPO, ownership, config)
    second, repeated = build_report(REPO, ownership, config)
    assert first == second
    args.output.mkdir()

    def document(name, value):
        (args.output / (name + ".json")).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")

    def table(name, rows):
        columns = list(dict.fromkeys(key for row in rows for key in row))
        with (args.output / (name + ".csv")).open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, columns, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)

    document("policy_manifest_schema", Registry.model_json_schema())
    document("drift_reference_manifest", {**first["reference"], "reference_sha256": digest(first["reference"])})
    document("dataset_manifest", {"splits": SPLITS, "canonical_inputs": first["datasets"]})
    document("evaluation_registry", first["evaluations"])
    document("acceptance_gates", {"governance_fixture": first["gates"], "phase10_requires_separate_approval": True,
                                 "notifications_sent": 0, "historical_release_blockers_open": True})
    for name, key in (("policy_compatibility_evaluation", "compatibility"), ("artifact_integrity_evaluation", "integrity"),
                      ("promotion_simulation", "promotion"), ("rollback_simulation", "rollback"),
                      ("telemetry_quality_evaluation", "quality"), ("drift_evaluation", "drift")):
        table(name, first[key])
    table("policy_transition_ownership", [{**{key: value for key, value in row.items() if key != "logical"},
          **row.get("logical", {})} for row in ownership["rows"]])
    table("offline_performance", timings + repeated)
    document("reproducibility_manifest", {
        "decision_sha256": digest(first), "ownership_decision_sha256": ownership["digest"],
        "matching_offline_runs": 2, "python": platform.python_version(), "dependencies": dependency_versions(),
        "output_sha256": {file.name: sha256(file.read_bytes()).hexdigest()
                          for file in sorted(args.output.iterdir())},
    })


if __name__ == "__main__":
    main()
