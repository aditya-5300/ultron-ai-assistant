"""Validate a pinned, non-executable release description and append evaluation records."""

from datetime import datetime
from hashlib import sha256
from importlib import metadata
import json
import os
from pathlib import Path
import tempfile
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from ultron_health_ml.contracts.telemetry import Contract, utc
from ultron_health_ml.service.policy import approved
from ultron_health_ml.service.schema import DETECTION_FP, FORECAST_FP

MAX_DOCUMENT = 2 * 1024 * 1024
APPROVED_IDENTITY = "e0f1a56ae6aa2733963add8251e0d1b5358dd7e859cf7e65c58b860b091608ea"
ARTIFACTS = {
    "bundle": "ml/src/ultron_health_ml/service/approved_policy.json",
    "loader": "ml/src/ultron_health_ml/service/policy.py",
    "engine": "ml/src/ultron_health_ml/service/engine.py",
    "transport": "ml/src/ultron_health_ml/service/app.py",
    "telemetry": "ml/src/ultron_health_ml/contracts/telemetry.py",
    "feature": "ml/src/ultron_health_ml/features/config.py",
    "feature_engine": "ml/src/ultron_health_ml/features/engineering.py",
    "feature_windows": "ml/src/ultron_health_ml/features/windows.py",
    "feature_checks": "ml/src/ultron_health_ml/features/validation.py",
    "feature_statistics": "ml/src/ultron_health_ml/features/statistics.py",
    "detector_settings": "ml/src/ultron_health_ml/hybrid/config.py",
    "detector_engine": "ml/src/ultron_health_ml/hybrid/detector.py",
    "detector_lifecycle": "ml/src/ultron_health_ml/reduced/policy.py",
    "forecast_settings": "ml/src/ultron_health_ml/forecast/contract.py",
    "forecast_engine": "ml/src/ultron_health_ml/forecast/baselines.py",
    "forecast_lifecycle": "ml/src/ultron_health_ml/forecast/lifecycle.py",
    "service": "ml/src/ultron_health_ml/service/schema.py",
    "eligibility": "lib/health/shadow/eligibility.cjs",
    "ownership": "lib/health/shadow/contract.cjs",
    "ledger": "lib/health/shadow/store.cjs",
    "phase8_evidence": "docs/phase8d-reference/results/acceptance_gates.json",
}
COMPONENTS = {
    "detection": ("phase45c-reduced-hybrid", "phase45c-v1"),
    "forecast": ("process-persistence", "phase5-targets-v1"),
    "features": ("ultron-feature-config", "1.0.0"),
    "telemetry": ("ultron-telemetry", "1.0.0"),
    "service": ("ultron-inference-api", "1.0.0"),
    "eligibility": ("ultron-offline-notification", "phase8-offline-v1"),
    "ownership": ("ultron-replay-ownership", "phase6-approved-v1"),
}
DEPENDENCIES = ("numpy", "pandas", "pydantic", "PyYAML", "fastapi", "uvicorn")
GATES = ("offline_evidence", "safety_isolation", "reproducibility", "ownership", "hypothetical_eligibility", "resource_limits")
Identifier = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Release = Literal["baseline", "candidate"]


class Rejected(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return sha256(canonical(value)).hexdigest()


def read_bytes(path, limit=MAX_DOCUMENT):
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise Rejected("RESOURCE_LIMIT")
    return data


def read_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise Rejected("DUPLICATE_JSON_KEY")
            result[key] = value
        return result

    def invalid_number(_):
        raise Rejected("INVALID_JSON_NUMBER")

    return json.loads(read_bytes(path), object_pairs_hook=unique, parse_constant=invalid_number)


def dependency_versions():
    try:
        return {name: metadata.version(name) for name in DEPENDENCIES}
    except metadata.PackageNotFoundError:
        raise Rejected("DEPENDENCY_MISSING") from None


class Manifest(Contract):
    schema_version: Literal["1.0.0"]
    policy_id: Literal["ultron-analytical-suite"]
    version: Literal["1.0.0", "1.0.1"]
    family: Literal["system-health"]
    kind: Literal["hybrid-rules-and-statistics"]
    created_at: datetime
    initial_state: Literal["EXPERIMENTAL", "APPROVED_FOR_SHADOW"]
    evaluation_reference: Identifier
    components: dict[str, tuple[str, str]] = Field(max_length=7)
    artifacts: dict[str, Digest] = Field(max_length=len(ARTIFACTS))
    dependencies: dict[str, Annotated[str, Field(pattern=r"^[0-9][a-zA-Z0-9.+-]{0,31}$")]] = Field(max_length=6)
    telemetry_schema: Literal["1.0.0"]
    feature_schema: Literal["1.0.0"]
    inference_api: Literal["1.0.0"]
    detection_fingerprint: Literal[DETECTION_FP]
    forecast_fingerprint: Literal[FORECAST_FP]
    detection: tuple[Literal["cpu", "disk_capacity", "process"], ...] = Field(max_length=3)
    forecasting: tuple[Literal["process"], ...] = Field(max_length=1)
    forecast_policy: Literal["persistence:1800:0.65"]
    lead_seconds: Literal[240]

    _utc = field_validator("created_at")(utc)

    @model_validator(mode="after")
    def complete(self):
        if (self.components != COMPONENTS or set(self.artifacts) != set(ARTIFACTS)
                or set(self.dependencies) != set(DEPENDENCIES)
                or self.detection != ("cpu", "disk_capacity", "process") or self.forecasting != ("process",)):
            raise ValueError("incomplete or incompatible manifest")
        return self

    @property
    def analytical_identity(self):
        return digest(self.model_dump(mode="json", exclude={
            "version", "created_at", "initial_state", "evaluation_reference"
        }))

    @property
    def manifest_hash(self):
        return digest(self.model_dump(mode="json"))


class Registry(Contract):
    schema_version: Literal["1.0.0"]
    entries: tuple[Manifest, Manifest]

    @model_validator(mode="after")
    def unique(self):
        if tuple((entry.version, entry.initial_state) for entry in self.entries) != (
                ("1.0.0", "APPROVED_FOR_SHADOW"), ("1.0.1", "EXPERIMENTAL")):
            raise ValueError("duplicate or unexpected registry entries")
        return self


def load_registry(path):
    try:
        return Registry.model_validate(read_json(path))
    except (OSError, ValueError, RecursionError) as error:
        raise Rejected("REGISTRY_INVALID_OR_UNAVAILABLE") from error


def verify(manifest, root, trusted, versions=None):
    if manifest.analytical_identity != APPROVED_IDENTITY or trusted.analytical_identity != APPROVED_IDENTITY:
        raise Rejected("UNAPPROVED_ANALYTICAL_IDENTITY")
    if (dependency_versions() if versions is None else versions) != manifest.dependencies:
        raise Rejected("DEPENDENCY_MISMATCH")
    try:
        root = root.resolve(strict=True)
    except OSError:
        raise Rejected("ARTIFACT_UNAVAILABLE") from None
    for handle, relative in ARTIFACTS.items():
        try:
            file = (root / relative).resolve(strict=True)
            if not file.is_relative_to(root):
                raise Rejected("ARTIFACT_OUTSIDE_ROOT")
            actual = sha256(read_bytes(file)).hexdigest()
        except OSError:
            raise Rejected("ARTIFACT_UNAVAILABLE") from None
        if actual != manifest.artifacts[handle]:
            raise Rejected("ARTIFACT_HASH_MISMATCH")
    try:
        approved()
    except (OSError, ValueError):
        raise Rejected("APPROVED_RUNTIME_INCOMPATIBLE") from None
    return manifest.analytical_identity


class Evaluation(Contract):
    schema_version: Literal["1.0.0"]
    evaluation_id: Identifier
    manifest_hash: Digest
    dataset_hash: Digest
    configuration_hash: Digest
    evidence_hash: Digest
    telemetry_schema: Literal["1.0.0"]
    feature_fingerprint: Literal[DETECTION_FP]
    forecast_fingerprint: Literal[FORECAST_FP]
    seed: int = Field(ge=0, le=2**32 - 1)
    python_version: Annotated[str, Field(pattern=r"^\d+\.\d+\.\d+$")]
    dependencies: dict[str, str] = Field(max_length=6)
    gates: dict[str, Literal["PASS", "FAIL", "INCONCLUSIVE"]] = Field(max_length=6)
    result: Literal["PASS", "FAIL", "INCONCLUSIVE"]
    metrics: dict[Identifier, int] = Field(max_length=24)

    @model_validator(mode="after")
    def valid_decision(self):
        values = set(self.gates.values())
        expected = "FAIL" if "FAIL" in values else "INCONCLUSIVE" if "INCONCLUSIVE" in values else "PASS"
        if set(self.gates) != set(GATES) or self.result != expected or set(self.dependencies) != set(DEPENDENCIES):
            raise ValueError("inconsistent evaluation")
        return self


def write_evaluation(directory: Path, record: Evaluation):
    data = canonical(record.model_dump(mode="json"))
    path = directory / (record.evaluation_id + ".json")
    with tempfile.TemporaryDirectory(dir=directory, prefix=".phase9-") as staging:
        pending = Path(staging) / "record"
        with pending.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(pending, path)
    return digest(record.model_dump(mode="json"))


Lifecycle = Literal["EXPERIMENTAL", "VALIDATED_OFFLINE", "APPROVED_FOR_SHADOW", "REJECTED", "RETIRED", "ROLLBACK_CANDIDATE"]


class Simulation:
    def __init__(self, registry, root, trusted):
        self.registry, self.root, self.trusted = registry, root, trusted
        self.baseline, self.candidate = registry.entries
        verify(self.baseline, root, trusted)
        self.selected = self.baseline
        self.state: Lifecycle = "EXPERIMENTAL"
        self.audit = []

    def _event(self, action, reason):
        row = {"action": action, "state": self.state, "selected": self.selected.version, "reason": reason}
        self.audit.append(row)
        return row

    def validate(self, record, evidence):
        if self.state != "EXPERIMENTAL":
            raise Rejected("INVALID_TRANSITION")
        try:
            verify(self.candidate, self.root, self.trusted)
        except Rejected as error:
            self.state = "REJECTED"
            return self._event("validate", str(error))
        if (record.manifest_hash != self.candidate.manifest_hash or record.evidence_hash != digest(evidence)
                or record.gates != evidence.get("gates")
                or record.dependencies != self.candidate.dependencies
                or record.evaluation_id != self.candidate.evaluation_reference):
            self.state = "REJECTED"
            return self._event("validate", "EVIDENCE_MISMATCH")
        self.state = "VALIDATED_OFFLINE" if record.result == "PASS" else "REJECTED"
        return self._event("validate", record.result)

    def promote(self, *, explicitly_approved=False):
        if self.state != "VALIDATED_OFFLINE" or explicitly_approved is not True:
            raise Rejected("PROMOTION_NOT_APPROVED")
        verify(self.candidate, self.root, self.trusted)
        self.state = "APPROVED_FOR_SHADOW"
        self.selected = self.candidate
        return self._event("simulate_promotion", "EXPLICIT_OFFLINE_APPROVAL")

    def rollback(self, reason, *, explicitly_approved=False):
        if (self.state != "APPROVED_FOR_SHADOW" or explicitly_approved is not True
                or reason not in {"INVALID_FINDINGS", "RESOURCE_LIMIT", "UNAVAILABLE", "INCOMPATIBLE"}):
            raise Rejected("ROLLBACK_NOT_APPROVED")
        try:
            verify(self.baseline, self.root, self.trusted)
        except Rejected:
            self.selected = None
            self.state = "ROLLBACK_CANDIDATE"
            row = {"action": "simulate_rollback", "state": self.state, "selected": None, "reason": "BASELINE_UNAVAILABLE"}
            self.audit.append(row)
            return row
        self.selected = self.baseline
        self.state = "ROLLBACK_CANDIDATE"
        return self._event("simulate_rollback", reason)

    def retire(self):
        if self.state not in {"REJECTED", "ROLLBACK_CANDIDATE"} or self.selected != self.baseline:
            raise Rejected("INVALID_TRANSITION")
        self.state = "RETIRED"
        return self._event("retire_candidate", "OFFLINE_ONLY")
