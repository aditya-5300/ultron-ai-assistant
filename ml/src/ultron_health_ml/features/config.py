"""Validated feature settings shared by offline runs and future inference."""

import hashlib
import json
from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, model_validator

from ultron_health_ml.contracts.telemetry import Contract, METRIC_SPECS, REAL_ULTRON_METRICS

FEATURE_SCHEMA_VERSION = "1.0.0"
Profile = Literal["real_ultron", "synthetic_extended"]
PROFILE_METRICS = {"real_ultron": tuple(REAL_ULTRON_METRICS), "synthetic_extended": tuple(METRIC_SPECS)}


class Threshold(Contract):
    value: float
    direction: Literal["high", "low"]


class FeatureConfig(Contract):
    profile: Profile = "real_ultron"
    metrics: tuple[str, ...] | None = None
    windows_seconds: tuple[int, ...] = (120, 300, 900, 3600)
    minimum_observations: int = Field(default=3, ge=2, le=50000)
    minimum_coverage: float = Field(default=0.5, gt=0, le=1)
    gap_factor: float = Field(default=2.5, ge=1, le=10)
    deep_interval_seconds: int = Field(default=300, ge=1, le=3600)
    max_cached_age_seconds: int = Field(default=660, ge=1, le=3600)
    baseline_seconds: int = Field(default=3600, ge=1, le=86400)
    baseline_min_observations: int = Field(default=5, ge=2, le=50000)
    baseline_min_coverage: float = Field(default=0.5, gt=0, le=1)
    minimum_trend_seconds: int = Field(default=60, ge=1, le=86400)
    cross_window_seconds: int = Field(default=900, ge=1, le=86400)
    epsilon: float = Field(default=1e-8, gt=0, le=1)
    constant_tolerance: float = Field(default=1e-8, ge=0)
    thresholds: dict[str, Threshold] = Field(default_factory=lambda: {
        "memory_usage_pct": Threshold(value=90, direction="high"),
        "disk_free_pct": Threshold(value=10, direction="low"),
    })

    @model_validator(mode="after")
    def validate_settings(self):
        allowed = PROFILE_METRICS[self.profile]
        if self.metrics is not None and (not self.metrics or len(set(self.metrics)) != len(self.metrics)
                                        or set(self.metrics) - set(allowed)):
            raise ValueError("metrics must be a nonempty, unique subset of the profile allowlist")
        if not 1 <= len(self.windows_seconds) <= 12 or tuple(sorted(set(self.windows_seconds))) != self.windows_seconds:
            raise ValueError("window durations must be unique, strictly increasing, and limited to 12")
        if not 1 <= self.windows_seconds[0] <= self.windows_seconds[-1] <= 86400:
            raise ValueError("windows must be between one second and one day")
        for name, threshold in self.thresholds.items():
            if name not in allowed:
                raise ValueError(f"threshold metric is not supported by this profile: {name}")
            lower, upper, *_ = METRIC_SPECS[name]
            if threshold.value < lower or (upper is not None and threshold.value > upper):
                raise ValueError(f"threshold out of bounds: {name}")
        return self

    def selected_metrics(self) -> tuple[str, ...]:
        return self.metrics if self.metrics is not None else PROFILE_METRICS[self.profile]

    def fingerprint(self) -> str:
        settings = self.model_dump(mode="json")
        settings["metrics"] = self.selected_metrics()
        payload = json.dumps({"feature_schema_version": FEATURE_SCHEMA_VERSION, "settings": settings},
                             sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_config(path: Path | None = None, profile: Profile | None = None) -> FeatureConfig:
    raw = {} if path is None else yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or set(raw) - {"features"} or not isinstance(raw.get("features", {}), dict):
        raise ValueError("YAML requires a features mapping")
    settings = dict(raw.get("features", {}))
    if profile is not None:
        settings["profile"] = profile
    return FeatureConfig.model_validate(settings)
