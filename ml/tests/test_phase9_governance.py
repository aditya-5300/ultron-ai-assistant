import hashlib
import json
from pathlib import Path
import platform
import shutil

import pytest
from pydantic import ValidationError

from ultron_health_ml.governance.drift import evaluate, make_reference, quality
from ultron_health_ml.governance.fixtures import SPLITS, fixture
from ultron_health_ml.governance.registry import (
    ARTIFACTS, GATES, Evaluation, Manifest, Registry, Rejected, Simulation, digest, load_registry, verify,
    write_evaluation,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def registry():
    return load_registry(ROOT / "docs/phase9-reference/policy_registry.json")


@pytest.fixture
def files(tmp_path):
    for relative in ARTIFACTS.values():
        output = tmp_path / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, output)
    return tmp_path


def record_for(policy, result="PASS"):
    gates = {gate: "PASS" for gate in GATES}
    gates["ownership"] = result
    evidence = {"gates": gates, "fixture": "phase9-tests"}
    return Evaluation.model_validate({
        "schema_version": "1.0.0", "evaluation_id": policy.evaluation_reference,
        "manifest_hash": policy.manifest_hash, "dataset_hash": digest("test"),
        "configuration_hash": digest("cfg"), "evidence_hash": digest(evidence),
        "telemetry_schema": policy.telemetry_schema, "feature_fingerprint": policy.detection_fingerprint,
        "forecast_fingerprint": policy.forecast_fingerprint, "seed": 9, "python_version": platform.python_version(),
        "dependencies": policy.dependencies, "gates": gates, "result": result, "metrics": {"duplicates": 0},
    }), evidence


@pytest.mark.parametrize(("field", "value"), [
    ("schema_version", "2.0.0"), ("version", "9.0.0"), ("telemetry_schema", "2.0.0"),
    ("feature_schema", "2.0.0"), ("detection_fingerprint", "0" * 64), ("forecast_fingerprint", "0" * 64),
    ("forecast_policy", "persistence:900:0.65"), ("lead_seconds", 0), ("forecasting", ["disk_capacity"]),
    ("detection", ["cpu"]), ("artifacts", {}), ("components", {}), ("dependencies", {}),
    ("command", "unsupported"),
])
def test_reject_invalid_manifest(registry, field, value):
    data = registry.entries[1].model_dump(mode="json")
    with pytest.raises(ValidationError):
        Manifest.model_validate({**data, field: value})


def test_registry_schema_and_duplicate_policy_version(registry):
    baseline, candidate = registry.entries
    assert verify(baseline, ROOT, baseline) == verify(candidate, ROOT, baseline)
    assert baseline.manifest_hash != candidate.manifest_hash
    with pytest.raises(ValidationError):
        Registry.model_validate({"schema_version": "1.0.0", "entries": [baseline, baseline]})
    assert "entries" in Registry.model_json_schema()["properties"]


def test_missing_corrupt_and_duplicate_json_registry(tmp_path):
    path = tmp_path / "registry.json"
    with pytest.raises(Rejected, match="REGISTRY_INVALID"):
        load_registry(path)
    for text in ('{', '{"schema_version":"1.0.0","schema_version":"1.0.0","entries":[]}'):
        path.write_text(text)
        with pytest.raises(Rejected, match="REGISTRY_INVALID"):
            load_registry(path)


def test_hashes_pinned_even_after_coordinated_manifest_edit(registry, files):
    baseline, candidate = registry.entries
    artifact = files / ARTIFACTS["bundle"]
    artifact.write_bytes(b"{}")
    with pytest.raises(Rejected, match="ARTIFACT_HASH_MISMATCH"):
        verify(candidate, files, baseline)
    changed = {**baseline.artifacts, "bundle": hashlib.sha256(b"{}").hexdigest()}
    forged = baseline.model_copy(update={"artifacts": changed})
    with pytest.raises(Rejected, match="UNAPPROVED_ANALYTICAL_IDENTITY"):
        verify(forged, files, forged)
    artifact.unlink()
    with pytest.raises(Rejected, match="ARTIFACT_UNAVAILABLE"):
        verify(candidate, files, baseline)


def test_dependency_failure(registry):
    baseline, candidate = registry.entries
    with pytest.raises(Rejected, match="DEPENDENCY_MISMATCH"):
        verify(candidate, ROOT, baseline, versions={})


@pytest.mark.parametrize("reason", ["INVALID_FINDINGS", "RESOURCE_LIMIT", "UNAVAILABLE", "INCOMPATIBLE"])
def test_explicit_promotion_and_rollback(registry, reason):
    simulation = Simulation(registry, ROOT, registry.entries[0])
    with pytest.raises(Rejected, match="PROMOTION_NOT_APPROVED"):
        simulation.promote(explicitly_approved=True)
    assert simulation.validate(*record_for(registry.entries[1]))["state"] == "VALIDATED_OFFLINE"
    with pytest.raises(Rejected, match="PROMOTION_NOT_APPROVED"):
        simulation.promote()
    assert simulation.promote(explicitly_approved=True)["selected"] == "1.0.1"
    with pytest.raises(Rejected, match="ROLLBACK_NOT_APPROVED"):
        simulation.rollback(reason)
    assert simulation.rollback(reason, explicitly_approved=True)["selected"] == "1.0.0"
    assert simulation.retire()["state"] == "RETIRED"


@pytest.mark.parametrize("outcome", ["FAIL", "INCONCLUSIVE"])
def test_negative_evaluation_retained_and_candidate_not_selected(registry, tmp_path, outcome):
    sim = Simulation(registry, ROOT, registry.entries[0])
    record, evidence = record_for(registry.entries[1], outcome)
    written = write_evaluation(tmp_path, record)
    file = tmp_path / (record.evaluation_id + ".json")
    before = file.read_bytes()
    assert hashlib.sha256(before).hexdigest() == written == digest(json.loads(before))
    with pytest.raises(FileExistsError):
        write_evaluation(tmp_path, record)
    with pytest.raises(OSError):
        write_evaluation(tmp_path / "missing", record)
    assert file.read_bytes() == before
    assert sim.validate(record, evidence)["state"] == "REJECTED"
    assert sim.selected == registry.entries[0]
    with pytest.raises(Rejected):
        sim.promote(explicitly_approved=True)


def test_wrong_evidence_and_failed_restore(registry, files):
    sim = Simulation(registry, files, registry.entries[0])
    record, evidence = record_for(registry.entries[1])
    assert sim.validate(record, {**evidence, "tampered": True})["reason"] == "EVIDENCE_MISMATCH"
    sim = Simulation(registry, files, registry.entries[0])
    sim.validate(record, evidence)
    sim.promote(explicitly_approved=True)
    (files / ARTIFACTS["loader"]).unlink()
    assert sim.rollback("UNAVAILABLE", explicitly_approved=True)["reason"] == "BASELINE_UNAVAILABLE"
    assert sim.selected is None


@pytest.mark.parametrize(("scenario", "status"), [
    ("stable", "NO_DRIFT_DETECTED"), ("shift", "DRIFT_OBSERVED"), ("missing", "INSUFFICIENT_DATA"),
    ("cached", "INSUFFICIENT_DATA"), ("constant", "NO_DRIFT_DETECTED"), ("sparse", "NO_DRIFT_DETECTED"),
    ("irregular", "DRIFT_OBSERVED"), ("context", "DRIFT_OBSERVED"), ("short", "INSUFFICIENT_DATA"),
])
def test_locked_drift_cases(scenario, status):
    reference = make_reference(fixture("reference", "stable"), "reference", SPLITS["reference"])
    candidate = fixture("locked", scenario)
    result = evaluate(candidate, reference, digest(reference))
    assert result["status"] == status
    assert result == evaluate(candidate, reference, digest(reference))
    if scenario == "cached":
        disk = result["quality"]["metrics"]["disk_free_pct"]
        assert disk["fresh"] == 1 and disk["cached"] > 0 and disk["stale"] > 0 and "median" not in disk
    if scenario == "missing":
        assert result["metrics"]["cpu_usage_pct"]["reasons"] == ["availability_shift"]
    if scenario == "irregular":
        assert "cadence_shift" in result["context_reasons"] and result["quality"]["polling_gaps"] > 0
    if scenario == "context":
        assert result["quality"]["unsupported_measurements"]["network_latency_ms"] == 180


def test_corrupt_reference_bad_schema_and_evaluator_error():
    window = fixture("locked", "stable")
    with pytest.raises(ValueError):
        make_reference(window, "locked", SPLITS["locked"])
    reference = make_reference(fixture("reference", "stable"), "reference", SPLITS["reference"])
    assert evaluate(window, reference, "0" * 64)["status"] == "REFERENCE_INCOMPATIBLE"
    broken = {**reference, "summary": {**reference["summary"], "context": {"unknown": 9}}}
    assert evaluate(window, broken, digest(broken))["status"] == "REFERENCE_INCOMPATIBLE"
    assert evaluate({"telemetry_schema_version": "9.0.0"}, reference, digest(reference))["status"] == "SCHEMA_INCOMPATIBLE"
    with pytest.raises(ValueError):
        quality({"records": []})
