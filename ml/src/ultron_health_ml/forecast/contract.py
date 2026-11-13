"""Versioned target definitions and causal actionable-state tracking."""

from dataclasses import dataclass
import json
from pathlib import Path

from pydantic import Field, model_validator
import yaml

from ultron_health_ml.contracts.telemetry import Contract
from ultron_health_ml.hybrid.detector import Evidence

SYSTEMS = ("cpu", "disk_capacity", "process")
METRICS = ("cpu_usage_pct", "memory_usage_pct", "disk_free_pct", "selected_process_nonresponding_count")
WORKLOADS = frozenset({"build", "backup", "short_build"})


class Target(Contract):
    event: str
    metric: str
    boundary: float
    direction: str
    context: str
    onset: str
    hold_seconds: int = Field(ge=0)
    min_observations: int = Field(ge=1)
    recovery_boundary: float
    recovery_seconds: int = Field(gt=0)
    max_gap_seconds: int = Field(gt=0)
    min_lead_seconds: int = Field(gt=0)
    horizon_seconds: tuple[int, ...]
    observability: str

    @model_validator(mode="after")
    def horizons(self):
        if (self.direction not in {"low", "high"} or not self.horizon_seconds
                or tuple(sorted(set(self.horizon_seconds))) != self.horizon_seconds
                or min(self.horizon_seconds) < self.min_lead_seconds):
            raise ValueError("invalid target direction or horizons")
        return self


class Targets(Contract):
    version: str
    clock: str
    future_window: str
    exclusions: str
    qualification: str
    targets: dict[str, Target]

    @model_validator(mode="after")
    def scope(self):
        if self.version != "phase5-targets-v1" or set(self.targets) != set(SYSTEMS):
            raise ValueError("unexpected target version or scope")
        if tuple(self.targets[key].metric for key in SYSTEMS) != (METRICS[0], METRICS[2], METRICS[3]):
            raise ValueError("unexpected target metrics")
        return self


class Experiment(Contract):
    development_seeds: tuple[int, ...]
    validation_seeds: tuple[int, ...]
    holdout_seeds: tuple[int, ...]
    hours_per_run: float = Field(ge=18, le=48)
    false_budget_per_day: float = Field(ge=0)
    workload_budget: float = Field(ge=0, le=1)
    min_useful_recall: float = Field(gt=0, le=1)
    min_precision: float = Field(gt=0, le=1)
    min_observable_events: int = Field(ge=1)

    @model_validator(mode="after")
    def independent(self):
        groups = (self.development_seeds, self.validation_seeds, self.holdout_seeds)
        seeds = sum(groups, ())
        if not all(groups) or len(set(seeds)) != len(seeds) or any(seed < 8000 for seed in seeds):
            raise ValueError("new, disjoint Phase 5 run seeds required")
        return self


def read_targets(path: Path):
    return Targets.model_validate(json.loads(path.read_text(encoding="utf8")))


def read_experiment(path: Path):
    return Experiment.model_validate(yaml.safe_load(path.read_text(encoding="utf8")))


@dataclass(frozen=True)
class Event:
    event_id: str
    subsystem: str
    onset: float
    recovery: float


def cpu_context(values):
    ram = values.get("memory_usage_pct")
    process = values.get("selected_process_nonresponding_count")
    if ram is None and process is None:
        return None
    if (ram is not None and ram >= 85) or (process is not None and process >= 2):
        return True
    return False if ram is not None and process is not None else None


class Actionable:
    """Tracks only observations at or before the current replay timestamp."""
    def __init__(self, key, target):
        self.key, self.target = key, target
        self.entry, self.exit = Evidence(), Evidence()
        self.active = False
        self.last = None

    def update(self, time, values, observed, fresh):
        target = self.target
        value = values.get(target.metric)
        context = cpu_context(values) if self.key == "cpu" else True
        if value is None or context is None or observed is None:
            self.entry.reset()
            self.exit.reset()
            return None
        if not fresh or (self.last is not None and observed <= self.last):
            return None
        self.last = observed
        crossed = value >= target.boundary if target.direction == "high" else value <= target.boundary
        recovered = value <= target.recovery_boundary if target.direction == "high" else value >= target.recovery_boundary
        if self.active:
            if recovered or context is False:
                self.exit.advance(observed, target.max_gap_seconds)
                if self.exit.count >= 2 and self.exit.duration >= target.recovery_seconds:
                    self.active = False
                    self.entry.reset()
                    self.exit.reset()
                    return "recovered"
            else:
                self.exit.reset()
        elif crossed and context:
            self.entry.advance(observed, target.max_gap_seconds)
            if self.entry.count >= target.min_observations and self.entry.duration >= target.hold_seconds:
                self.active = True
                self.exit.reset()
                return "actionable"
        else:
            self.entry.reset()
        return None
