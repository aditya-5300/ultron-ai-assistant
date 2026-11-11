"""Validated generator settings; all times are offsets from a fixed UTC start."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, field_validator, model_validator

from ultron_health_ml.contracts.telemetry import Contract, METRIC_SPECS, utc

ScenarioType = Literal[
    "NORMAL", "CPU_SPIKE", "SUSTAINED_CPU_PRESSURE", "MEMORY_LEAK",
    "DISK_EXHAUSTION", "NETWORK_DEGRADATION", "RESPONSE_TIME_DEGRADATION",
    "ERROR_BURST", "THROUGHPUT_SURGE", "MULTI_METRIC_DEGRADATION",
    "BACKUP_WORKLOAD", "COMPILATION_OR_BUILD_WORKLOAD",
]
SCENARIO_TYPES = (
    "NORMAL", "CPU_SPIKE", "SUSTAINED_CPU_PRESSURE", "MEMORY_LEAK",
    "DISK_EXHAUSTION", "NETWORK_DEGRADATION", "RESPONSE_TIME_DEGRADATION",
    "ERROR_BURST", "THROUGHPUT_SURGE", "MULTI_METRIC_DEGRADATION",
    "BACKUP_WORKLOAD", "COMPILATION_OR_BUILD_WORKLOAD",
)


class ScenarioConfig(Contract):
    scenario_type: ScenarioType
    enabled: bool = True
    start_fraction: float = Field(ge=0, lt=1)
    duration_fraction: float = Field(gt=0, le=1)
    intensity: float = Field(default=1, gt=0, le=3)

    @model_validator(mode="after")
    def fits_duration(self):
        if self.start_fraction + self.duration_fraction > 1 + 1e-12:
            raise ValueError("scenario extends beyond dataset duration")
        return self


class QualityEvent(Contract):
    metric: str
    kind: Literal["MISSING_SENSOR", "DEEP_COLLECTION_OUTAGE"]
    start_fraction: float = Field(ge=0, lt=1)
    duration_fraction: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def valid_event(self):
        from ultron_health_ml.contracts.telemetry import DEEP_METRICS

        if self.metric not in METRIC_SPECS:
            raise ValueError("unknown quality-event metric")
        if self.kind == "DEEP_COLLECTION_OUTAGE" and self.metric not in DEEP_METRICS:
            raise ValueError("deep outages only apply to deep metrics")
        if self.start_fraction + self.duration_fraction > 1 + 1e-12:
            raise ValueError("quality event extends beyond dataset")
        return self


class NoiseConfig(Contract):
    cpu: float = Field(default=2.5, ge=0, le=15)
    memory: float = Field(default=0.12, ge=0, le=2)
    network: float = Field(default=1.5, ge=0, le=15)


class IncidentThresholds(Contract):
    memory_pct: float = Field(default=90, gt=0, le=100)
    disk_free_pct: float = Field(default=10, ge=0, lt=100)
    cpu_pct: float = Field(default=85, gt=0, le=100)
    sustained_seconds: float = Field(default=300, gt=0)
    network_latency_ms: float = Field(default=180, gt=0)
    response_time_ms: float = Field(default=800, gt=0)
    error_rate: float = Field(default=0.1, gt=0, le=1)


def default_scenarios():
    # Disjoint windows leave recovery periods; CPU spikes stay short even for seven days.
    return tuple(
        ScenarioConfig(scenario_type=name, start_fraction=0.02 + index * 0.08,
                       duration_fraction=0.001 if name == "CPU_SPIKE" else 0.045)
        for index, name in enumerate(SCENARIO_TYPES)
    )


class GeneratorConfig(Contract):
    days: float = Field(default=7, gt=0, le=365)
    interval_seconds: Literal[15, 30, 60, 120, 300] = 30
    random_seed: int = Field(default=42, ge=0, le=2**32 - 1)
    start_time: datetime = datetime(2026, 1, 1, tzinfo=timezone.utc)
    jitter_seconds: float = Field(default=0, ge=0)
    skip_probability: float = Field(default=0, ge=0, lt=0.95)
    overrun_probability: float = Field(default=0, ge=0, le=1)
    overrun_seconds: float = Field(default=45, ge=0, le=600)
    missing_probability: float = Field(default=0.002, ge=0, lt=1)
    deep_interval_seconds: float = Field(default=300, gt=0)
    deep_stale_after_seconds: float = Field(default=660, gt=0)
    noise: NoiseConfig = Field(default_factory=NoiseConfig)
    incidents: IncidentThresholds = Field(default_factory=IncidentThresholds)
    scenarios: tuple[ScenarioConfig, ...] = Field(default_factory=default_scenarios)
    quality_events: tuple[QualityEvent, ...] = ()

    @field_validator("start_time")
    @classmethod
    def fixed_utc_start(cls, value):
        return utc(value)

    @model_validator(mode="after")
    def reasonable_size(self):
        if self.jitter_seconds >= self.interval_seconds / 2:
            raise ValueError("jitter must be less than half the nominal interval")
        if self.deep_stale_after_seconds <= self.deep_interval_seconds:
            raise ValueError("deep stale threshold must exceed refresh interval")
        minimum_spacing = self.interval_seconds - self.jitter_seconds
        if self.days * 86400 / minimum_spacing > 50000:
            raise ValueError("dataset too large; split into bounded generation runs")
        return self


def load_config(path: Path | None = None, **overrides) -> GeneratorConfig:
    raw = {} if path is None else yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or set(raw) - {"generator"}:
        raise ValueError("YAML requires a generator mapping, with no unknown top-level keys")
    settings = raw.get("generator", {})
    if not isinstance(settings, dict):
        raise ValueError("generator settings must be a mapping")
    return GeneratorConfig.model_validate({**settings, **{key: value for key, value in overrides.items() if value is not None}})
