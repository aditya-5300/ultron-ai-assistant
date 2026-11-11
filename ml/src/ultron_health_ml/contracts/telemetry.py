"""Contract only: no imputation, learned features, labels, or database access."""

from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TELEMETRY_SCHEMA_VERSION = "1.0.0"

# These are sanitized concepts supported by the existing collector, not new sensors.
REAL_ULTRON_METRICS = {
    "cpu_usage_pct": (0.0, 100.0, "%", "cpuPct"),
    "memory_usage_pct": (0.0, 100.0, "%", "memory.usedPct"),
    "disk_free_pct": (0.0, 100.0, "%", "volumes[].freePct; one selected volume"),
    "battery_pct": (0.0, 100.0, "%", "battery.percent"),
    "battery_capacity_pct": (0.0, None, "%", "100 * fullCapacity / designCapacity"),
    "disk_temperature_c": (0.0, 150.0, "C", "disks[].temperatureC; one selected disk"),
    "selected_process_nonresponding_count": (0.0, 24.0, "count", "selected processes only"),
    "relevant_event_count": (0.0, 80.0, "count", "bounded System/Application log metadata"),
}
SYNTHETIC_APPLICATION_METRICS = {
    "network_latency_ms": (0.0, None, "ms", "not collected by health worker"),
    "response_time_ms": (0.0, None, "ms", "not collected by health worker"),
    "error_rate": (0.0, 1.0, "fraction", "not collected by health worker"),
    "throughput_rps": (0.0, None, "requests/s", "not collected by health worker"),
    "disk_write_mbps": (0.0, None, "MB/s", "synthetic workload illustration only"),
}
METRIC_SPECS = REAL_ULTRON_METRICS | SYNTHETIC_APPLICATION_METRICS
DEEP_METRICS = frozenset({"disk_free_pct", "battery_capacity_pct", "disk_temperature_c", "relevant_event_count"})
# A numeric allowlist, not a Phase 3 feature vector. Evaluation metadata is elsewhere.
DECLARED_MODEL_INPUT_FIELDS = tuple(METRIC_SPECS)


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False, revalidate_instances="always")


def utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("timestamps must have an explicit UTC offset")
    return value.astimezone(timezone.utc)


class MetricReading(Contract):
    value: float | None
    observation_time: datetime | None
    availability: Literal["available", "unavailable"]
    quality: Literal["fresh", "cached", "missing", "stale"]

    @field_validator("observation_time")
    @classmethod
    def observation_is_utc(cls, value: datetime | None) -> datetime | None:
        return utc(value) if value is not None else None

    @field_validator("value", mode="before")
    @classmethod
    def numeric_not_boolean(cls, value):
        if isinstance(value, bool) or (value is not None and not isinstance(value, (int, float))):
            raise ValueError("metric must be a number or null")
        return value

    @model_validator(mode="after")
    def valid_state(self):
        if self.quality == "missing":
            if self.value is not None or self.observation_time is not None or self.availability != "unavailable":
                raise ValueError("missing readings need null value/time and unavailable coverage")
        elif self.value is None or self.observation_time is None or self.availability != "available":
            raise ValueError("observed readings require value/time and available coverage")
        # Stale values are retained for diagnostics only, not declared usable input.
        return self

    def usable_value(self) -> float | None:
        return self.value if self.quality in {"fresh", "cached"} else None


class TelemetryRecord(Contract):
    telemetry_schema_version: Literal["1.0.0"] = TELEMETRY_SCHEMA_VERSION
    source: Literal["synthetic", "ultron"]
    source_version: str = Field(min_length=1, max_length=100)
    sample_id: str = Field(min_length=1, max_length=100)
    sample_time: datetime
    nominal_interval_seconds: Literal[15, 30, 60, 120, 300]
    elapsed_seconds: float | None = Field(default=None, gt=0)
    collection_duration_seconds: float = Field(default=0, ge=0)
    ac_online: bool | None = None
    power_plan: Literal["balanced", "performance", "saver", "unknown"] = "unknown"
    metrics: dict[str, MetricReading]

    @field_validator("sample_time")
    @classmethod
    def sample_is_utc(cls, value):
        return utc(value)

    @model_validator(mode="after")
    def valid_metrics(self):
        if not self.metrics or set(self.metrics) - METRIC_SPECS.keys():
            raise ValueError("unknown metrics or empty metric set")
        if self.source == "ultron" and set(self.metrics) & SYNTHETIC_APPLICATION_METRICS.keys():
            raise ValueError("real ULTRON does not collect synthetic application metrics")
        for name, reading in self.metrics.items():
            if reading.observation_time and reading.observation_time > self.sample_time:
                raise ValueError("sensor observation cannot be later than sample_time")
            if reading.quality == "fresh" and reading.observation_time != self.sample_time:
                raise ValueError("fresh readings must match sample_time; older readings are cached/stale")
            if reading.quality in {"cached", "stale"} and reading.observation_time >= self.sample_time:
                raise ValueError("cached/stale readings must have older observation times")
            lower, upper, *_ = METRIC_SPECS[name]
            if reading.value is not None:
                if reading.value < lower or (upper is not None and reading.value > upper):
                    raise ValueError(f"{name} is out of bounds")
                if name.endswith("_count") and not reading.value.is_integer():
                    raise ValueError("count metrics must be whole numbers")
        return self


class TelemetryWindow(Contract):
    telemetry_schema_version: Literal["1.0.0"] = TELEMETRY_SCHEMA_VERSION
    window_start: datetime
    window_end: datetime
    as_of: datetime
    records: tuple[TelemetryRecord, ...] = Field(min_length=1, max_length=50000)

    @field_validator("window_start", "window_end", "as_of")
    @classmethod
    def bounds_are_utc(cls, value):
        return utc(value)

    @model_validator(mode="after")
    def chronological_past_only(self):
        if not self.window_start <= self.window_end <= self.as_of:
            raise ValueError("require window_start <= window_end <= as_of")
        previous = None
        identities = set()
        sources = set()
        for record in self.records:
            if not self.window_start <= record.sample_time <= self.window_end:
                raise ValueError("sample outside bounded window/as_of")
            if previous and record.sample_time <= previous.sample_time:
                raise ValueError("duplicate or out-of-order sample timestamps")
            if previous:
                elapsed = (record.sample_time - previous.sample_time).total_seconds()
                if record.elapsed_seconds is None or abs(elapsed - record.elapsed_seconds) > 0.001:
                    raise ValueError("elapsed_seconds must match actual timestamp gaps")
            if record.sample_id in identities:
                raise ValueError("duplicate sample_id")
            identities.add(record.sample_id)
            sources.add((record.source, record.source_version))
            previous = record
        if len(sources) != 1:
            raise ValueError("a window must have one declared source/version")
        return self
