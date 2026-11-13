"""Explicit replay and finding contracts for the loopback service."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, model_validator

from ultron_health_ml.contracts.telemetry import Contract, REAL_ULTRON_METRICS, TelemetryRecord, TelemetryWindow

API_VERSION = "1.0.0"
DETECTION_FP = "d9c14284e9349439e07f632aaa91f077a2a8f662afb7ef9939b9666c8024ef47"
FORECAST_FP = "3a95a6eb5d0dae388f906c3ef4e14440a8d5ef0aab549a7dc34ca6f4304ed1ff"
POLICY_VERSION = "phase6-approved-v1"
MAX_ROWS = 1800
MAX_HISTORY_SECONDS = 43200
MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_FUTURE_SECONDS = 2
Identifier = Annotated[str, Field(strict=True, min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")]
Subsystem = Literal["cpu", "disk_capacity", "process"]


class ReplayWindow(TelemetryWindow):
    telemetry_schema_version: Literal["1.0.0"]
    records: Annotated[tuple[TelemetryRecord, ...], Field(min_length=2, max_length=MAX_ROWS)]

    @model_validator(mode="after")
    def bounds(self):
        if self.window_start != self.records[0].sample_time or self.window_end != self.records[-1].sample_time:
            raise ValueError("window bounds must equal first and last acquisition")
        if self.window_end != self.as_of or (self.as_of - self.window_start).total_seconds() > MAX_HISTORY_SECONDS:
            raise ValueError("invalid replay duration or as-of boundary")
        if self.records[0].elapsed_seconds is not None:
            raise ValueError("bounded replay must begin with a null elapsed interval")
        if any(set(row.metrics) - REAL_ULTRON_METRICS.keys() for row in self.records):
            raise ValueError("only real_ultron metric fields are accepted")
        return self


class Fingerprints(Contract):
    detection: Literal[DETECTION_FP]
    forecast: Literal[FORECAST_FP]


class Scope(Contract):
    detection: tuple[Subsystem, ...] = Field(max_length=3)
    forecasting: tuple[Literal["process"], ...] = Field(max_length=1)

    @model_validator(mode="after")
    def check(self):
        if (not self.detection and not self.forecasting) or len(set(self.detection)) != len(self.detection):
            raise ValueError("scope must be nonempty and unique")
        return self


class InferRequest(Contract):
    api_version: Literal[API_VERSION]
    feature_schema_version: Literal["1.0.0"]
    feature_fingerprints: Fingerprints
    request_id: Identifier
    replay_id: Identifier
    mode: Literal["bounded_replay"]
    scope: Scope
    telemetry: ReplayWindow


class EvidenceSummary(Contract):
    available: bool
    reading: float | None = None
    quality: Literal["fresh", "cached", "unavailable"]
    independent_confirmations: int | None = None
    score: float | None = None
    score_type: Literal["uncalibrated_evidence"] | None = None


class Finding(Contract):
    finding_id: str
    event_id: str
    subsystem: Subsystem
    finding_type: Literal["DETECTION", "FORECAST", "INSUFFICIENT_EVIDENCE"]
    transition: Literal["OPEN", "UPDATE", "ESCALATE", "CLOSE", "INSUFFICIENT_EVIDENCE"]
    state: Literal["active", "closed", "insufficient_evidence"]
    severity: Literal["advisory", "early_warning", "critical_reference", "none"]
    first_at: datetime
    latest_evidence_at: datetime | None
    decision_at: datetime
    definition_version: str
    source: Literal["reduced_hybrid", "process_persistence", "process_actionable", "coverage"]
    evidence: EvidenceSummary
    horizon_seconds: Literal[1800] | None = None
    minimum_useful_lead_seconds: Literal[240] | None = None
    target_window_end: datetime | None = None
    reason: str | None = None


class InferResponse(Contract):
    api_version: Literal[API_VERSION] = API_VERSION
    telemetry_schema_version: Literal["1.0.0"] = "1.0.0"
    feature_schema_version: Literal["1.0.0"] = "1.0.0"
    feature_fingerprints: Fingerprints
    policy_version: Literal[POLICY_VERSION] = POLICY_VERSION
    request_id: Identifier
    replay_id: Identifier
    as_of: datetime
    findings: tuple[Finding, ...]
    process_raw_qualifying_rows: int
    logical_open_count: int
    advisory_only: Literal[True] = True
