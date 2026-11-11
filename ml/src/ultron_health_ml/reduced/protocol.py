"""Predeclared scope, operating points, and event-level selection."""

from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import Field, model_validator

from ultron_health_ml.contracts.telemetry import Contract
from ultron_health_ml.hybrid.config import Settings, load_settings

PROTOCOL = "phase45c-v1"
SCOPE = ("cpu", "disk_capacity", "process")
METHODS = ("fixed", "mad", "sustained", "hybrid")
RULES = {
    "budget": "At most 0.5 false WARNING/CRITICAL episodes/device-day and 5% backup/build session nuisance.",
    "selection": "For each family, select the best point qualifying on both development and validation. "
                 "Rank by validation recall, median delay, p95 delay, lower false episodes, lower workload nuisance, "
                 "then grid order. Otherwise freeze the closest validation point as diagnostic only.",
    "useful": "A simpler method needs at least 20% scoped recall and must pass safety and budget at its locked point.",
    "matching": "One episode per same-subsystem actionable event; first notice in [actionable onset, event end). "
                "Early OBSERVE is silent. Scope excludes RAM safety from primary recall, not from safety or nuisance.",
    "value": "Hybrid must pass safety and budget, achieve >=50% scoped recall, and beat every useful budget-qualified "
             "simpler point by >=10 percentage points recall or >=20% lower median delay on common detected events "
             "without >5 point overall recall loss. No subsystem may lose >10 points. "
             "Require >=10 events per subsystem, >=8 runs, and positive lower paired bootstrap bound for the winning metric.",
    "noninferiority": "Descriptive: simpler method within 10 points overall and in each subsystem, and common-hit median "
                      "delay <=125% of hybrid; at least 10 common events, same alert cap.",
    "uncertainty": "Percentile bootstrap over paired holdout runs (1000 replicates, seed 45103). Descriptive synthetic uncertainty.",
    "decision": "GO only when safety, nuisance, value, reproducibility and latency pass. Repeat for concrete missing "
                "evidence or a nonqualifying hybrid without a useful simpler substitute; stop for a qualified noninferior simpler point.",
}


@dataclass(frozen=True)
class Point:
    method: str
    level: float
    persistence: float

    @property
    def name(self):
        return f"{self.method}-t{self.level:g}-p{self.persistence:g}"


POINTS = tuple(Point(method, level, persistence) for method in METHODS for level, persistence in (
    (.85, 1.), (1., 1.), (1., 1.5), (1.2, 1.5), (1.35, 2.), (1.5, 2.5),
))


class Experiment(Contract):
    protocol: str
    development_seeds: tuple[int, ...]
    validation_seeds: tuple[int, ...]
    holdout_seeds: tuple[int, ...]
    hours_per_run: float = Field(ge=8, le=24)

    @model_validator(mode="after")
    def independent(self):
        groups = (self.development_seeds, self.validation_seeds, self.holdout_seeds)
        seeds = sum(groups, ())
        if (self.protocol != PROTOCOL or not all(groups) or len(set(seeds)) != len(seeds)
                or any(not 7000 <= seed < 2**32 for seed in seeds)):
            raise ValueError("C requires new nonoverlapping seeds in all partitions")
        return self

    def replay_config(self, base: Path):
        return Settings.model_validate(dict(load_settings(base).model_dump(), train_seeds=self.development_seeds,
            validation_seeds=self.validation_seeds, holdout_seeds=self.holdout_seeds, hours_per_run=self.hours_per_run))


def read_experiment(path):
    return Experiment.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def safety(result):
    return result["critical_expected"] > 0 and not any(result[key] for key in (
        "safety_misses", "critical_missed", "critical_delayed",
    ))


def budget(result, config):
    return (result["workload_nuisance_rate"] is not None
            and result["false_episodes_per_day"] <= config.false_budget_per_day
            and result["workload_nuisance_rate"] <= config.workload_budget)


def choose(points, development, validation, config):
    def quality(point):
        result = validation[point.name]
        median, tail = result["median_delay_seconds"], result["p95_delay_seconds"]
        return (result["event_recall"], -median if median is not None else -1e9,
                -tail if tail is not None else -1e9, -result["false_episodes_per_day"],
                -result["workload_nuisance_rate"])

    eligible = [point for point in points if all(safety(split[point.name]) and budget(split[point.name], config)
                                                for split in (development, validation))]
    best = max(eligible, key=quality) if eligible else None

    def excess(point):
        result = validation[point.name]
        return (safety(result), -max(0, result["false_episodes_per_day"] - config.false_budget_per_day),
                -max(0, result["workload_nuisance_rate"] - config.workload_budget), *quality(point))

    over_budget = [point for point in points if not budget(validation[point.name], config)]
    closest = max(over_budget, key=excess, default=None)
    return best or max(points, key=excess), bool(best), closest
