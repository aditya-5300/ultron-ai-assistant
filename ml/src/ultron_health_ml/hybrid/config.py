"""Versioned, validated offline detector and evaluation configuration."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, model_validator

from ultron_health_ml.contracts.telemetry import Contract, REAL_ULTRON_METRICS

SCHEMA_VERSION = "1.0.0"
WORKLOADS = frozenset({"build", "short_build", "backup"})
PRIMARY_MODES = ("mad", "sustained", "ewma", "cusum", "hybrid")
CRITICAL_LIMITS = {"memory": ("memory_usage_pct", 95., "high"),
                   "disk_capacity": ("disk_free_pct", 5., "low")}


class Subsystem(Contract):
    metric: str
    direction: Literal["high", "low", "absolute"]
    attention_level: float
    mad_z: float = Field(default=4, gt=0)
    mad_history_points: int = Field(default=5, ge=3)
    noise_floor: float = Field(gt=0)
    trend_window_seconds: Literal[120, 300, 900, 3600] = 900
    minimum_slope_per_hour: float = Field(gt=0)
    minimum_delta: float = Field(gt=0)
    min_observations: int = Field(default=3, ge=2)
    min_duration: float = Field(gt=0)
    recovery_seconds: float = Field(gt=0)
    max_observation_gap: float = Field(gt=0)
    ewma_shift: float = Field(gt=0)
    cusum_drift: float = Field(default=0.5, gt=0)
    cusum_limit: float = Field(default=8, gt=0)
    modes: tuple[Literal["mad", "sustained", "ewma", "cusum"], ...] = ()
    require_unplugged: bool = False

    @model_validator(mode="after")
    def check_metric(self):
        if self.metric not in REAL_ULTRON_METRICS:
            raise ValueError("only real_ultron metrics are supported")
        if len(set(self.modes)) != len(self.modes):
            raise ValueError("duplicate detector mode")
        return self


class Settings(Contract):
    train_seeds: tuple[int, ...]
    validation_seeds: tuple[int, ...]
    holdout_seeds: tuple[int, ...]
    hours_per_run: float = Field(default=12, ge=4, le=24)
    candidates: tuple[float, ...] = (1., 1.25, 1.5, 2.)
    false_budget_per_day: float = Field(default=0.5, gt=0)
    workload_budget: float = Field(default=0.05, gt=0, le=1)
    budget_match_tolerance: float = Field(default=0.5, ge=0)
    recovery_fraction: float = Field(default=0.55, gt=0, lt=1)
    missing_grace_seconds: float = Field(default=180, ge=0)
    flap_window_seconds: float = Field(default=600, ge=0)
    ewma_seconds: float = Field(default=150, gt=0)
    reference_seconds: float = Field(default=1800, gt=0)
    subsystems: dict[str, Subsystem]

    @model_validator(mode="after")
    def validate_protocol(self):
        groups = (self.train_seeds, self.validation_seeds, self.holdout_seeds)
        seeds = sum(groups, ())
        if (any(not group for group in groups) or len(set(seeds)) != len(seeds)
                or any(not 1000 <= seed < 2**32 for seed in seeds)):
            raise ValueError("new, independent Phase 4.5 seeds are required")
        if (not self.candidates or tuple(sorted(set(self.candidates))) != self.candidates
                or any(not 1 <= value <= 10 for value in self.candidates)):
            raise ValueError("candidates must be increasing threshold multipliers in [1, 10]")
        for key, (metric, _, _) in CRITICAL_LIMITS.items():
            if key not in self.subsystems or self.subsystems[key].metric != metric:
                raise ValueError(f"required critical subsystem is missing: {key}")
        if self.reference_seconds <= self.ewma_seconds:
            raise ValueError("reference must be slower than EWMA")
        return self


def load_settings(path: Path) -> Settings:
    return Settings.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
