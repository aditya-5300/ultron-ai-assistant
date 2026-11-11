"""Versioned settings for the offline experiment."""
from pathlib import Path

import yaml
from pydantic import Field, model_validator

from ultron_health_ml.contracts.telemetry import Contract

MODEL_VERSION = "0.1.0"
MODEL_NAME = "ultron_health_isolation_forest"
SCORE_ORIENTATION = "higher_is_more_anomalous"


class Candidate(Contract):
    n_estimators: int = Field(default=100, ge=1)
    max_samples: float | str = "auto"
    max_features: float = Field(default=1, gt=0, le=1)
    scale: bool = False

    @model_validator(mode="after")
    def validate_samples(self):
        if self.max_samples != "auto" and not (
            isinstance(self.max_samples, float) and 0 < self.max_samples <= 1
        ):
            raise ValueError("max_samples must be auto or a fraction in (0, 1]")
        return self


class TrainingConfig(Contract):
    seed: int = Field(default=42, ge=0, le=2**32 - 1)
    train_seeds: tuple[int, ...] = (101, 102)
    validation_seeds: tuple[int, ...] = (201, 202)
    test_seeds: tuple[int, ...] = (301, 302)
    days_per_run: float = Field(default=0.25, ge=0.045, le=2)
    max_missing_fraction: float = Field(default=0.45, ge=0, lt=1)
    min_observations: int = Field(default=12, ge=2)
    near_constant_tolerance: float = Field(default=1e-8, ge=0)
    excluded_features: tuple[str, ...] = ()
    episode_cooldown_seconds: int = Field(default=60, ge=0)
    max_gap_seconds: int = Field(default=90, gt=0)
    cpu_limit_pct: float = Field(default=85, gt=0, le=100)
    memory_limit_pct: float = Field(default=90, gt=0, le=100)
    disk_free_limit_pct: float = Field(default=10, ge=0, lt=100)
    mad_cutoff: float = Field(default=4, gt=0)
    mad_persistence: int = Field(default=2, ge=1)
    workload_penalty: float = Field(default=0.5, ge=0)
    false_positive_penalty: float = Field(default=0.25, ge=0)
    candidates: tuple[Candidate, ...] = (
        Candidate(), Candidate(n_estimators=200, max_samples=0.5, max_features=0.75),
        Candidate(scale=True),
    )

    @model_validator(mode="after")
    def check_splits(self):
        groups = (self.train_seeds, self.validation_seeds, self.test_seeds)
        seeds = sum(groups, ())
        if any(not group for group in groups) or len(set(seeds)) != len(seeds):
            raise ValueError("split seeds must be nonempty and disjoint")
        if any(not 0 <= seed <= 2**32 - 1 for seed in seeds) or not self.candidates:
            raise ValueError("invalid run seed or empty candidate list")
        return self


def load_config(path: Path | None) -> TrainingConfig:
    return TrainingConfig() if path is None else TrainingConfig.model_validate(
        yaml.safe_load(path.read_text(encoding="utf-8"))
    )
