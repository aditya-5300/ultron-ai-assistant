"""Predeclared selection, episode scoring, and release gates."""

from pathlib import Path

from pydantic import Field, model_validator
import yaml

from ultron_health_ml.contracts.telemetry import Contract
from ultron_health_ml.hybrid.config import Settings, load_settings

PROTOCOL = "phase45b-v1"
OBJECTIVE = {
    "critical": "Ineligible with any observed critical drop or delay.",
    "budget": "Ineligible with >0.5 false interruptions per monitored device-day or >5% backup/build nuisance.",
    "utility": "100 * event recall - 15 * false interruptions/day - 100 * workload nuisance rate "
               "- 8 * (flaps + duplicate advisories)/day - 0.005 * median delay seconds.",
    "selection": "Rank policies on development; on validation select the best eligible from the top four including ties. "
                 "If none are eligible, select the least over budget for diagnostic replay only. "
                 "Tie-break by lower nuisance, then configuration order.",
    "early_observe": "OBSERVE never counts as a detection or interruption. A WARNING before actionable onset "
                     "is a false interruption; no credit for merely remaining open at onset.",
    "warning_matching": "First same-subsystem WARNING or CRITICAL notice in the actionable window; "
                        "an episode can be matched to one event. Associated evidence is not a second alert.",
    "value_gate": "At least 50% all-event recall and no more than one fewer event than any safety-qualified, "
                  "budget-qualified fixed/MAD/sustained comparator; no approval without a qualifying comparator.",
    "budget_units": "Raw workload sessions and monitored device-days; exclude unobserved sleep time.",
}
POLICIES = {
    "sustained_baseline": (),
    "lifecycle": ("lifecycle",),
    "without_drain": ("lifecycle", "no_drain"),
    "strong_memory": ("lifecycle", "memory"),
    "strong_process": ("lifecycle", "process"),
    "cpu_process_fusion": ("lifecycle", "fusion"),
    "memory_process": ("lifecycle", "memory", "process"),
    "core": ("lifecycle", "memory", "process", "fusion", "scope"),
    "core_mad": ("lifecycle", "memory", "process", "fusion", "scope", "mad"),
    "core_ewma": ("lifecycle", "memory", "process", "fusion", "scope", "ewma"),
    "core_cusum": ("lifecycle", "memory", "process", "fusion", "scope", "cusum"),
    "fixed_budget": (),
    "mad_budget": (),
}
CANDIDATES = tuple(name for name in POLICIES if name not in {"fixed_budget", "mad_budget"})
COMPARATORS = ("fixed_budget", "mad_budget", "sustained_baseline")


class Experiment(Contract):
    protocol: str
    development_seeds: tuple[int, ...]
    validation_seeds: tuple[int, ...]
    holdout_seeds: tuple[int, ...]
    hours_per_run: float = Field(ge=4, le=24)

    @model_validator(mode="after")
    def check(self):
        groups = (self.development_seeds, self.validation_seeds, self.holdout_seeds)
        all_seeds = sum(groups, ())
        if (self.protocol != PROTOCOL or not all(groups) or len(set(all_seeds)) != len(all_seeds)
                or any(not 6000 <= seed < 2**32 for seed in all_seeds)):
            raise ValueError("Phase 4.5-B needs three distinct new seed groups")
        return self

    def replay_config(self, base: Path):
        fields = load_settings(base).model_dump()
        return Settings.model_validate(dict(
            fields, train_seeds=self.development_seeds, validation_seeds=self.validation_seeds,
            holdout_seeds=self.holdout_seeds, hours_per_run=self.hours_per_run,
        ))


def read_experiment(path):
    return Experiment.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def safety(result):
    return not any(result[key] for key in ("safety_misses", "critical_missed", "critical_delayed",
                                          "false_critical_episodes"))


def budget(result, settings):
    rate = result["workload_nuisance_rate"]
    return (result["false_episodes_per_day"] <= settings.false_budget_per_day
            and rate is not None and rate <= settings.workload_budget)


def objective(result, settings):
    rate = result["false_episodes_per_day"]
    workload = result["workload_nuisance_rate"]
    workload = workload if workload is not None else 1.
    days = max(result["monitored_device_days"], 1e-9)
    delay = result["median_delay_seconds"]
    utility = (100 * (result["event_recall"] or 0.) - 15 * rate - 100 * workload
               - 8 * (result["flap_count"] + result["duplicate_advisories"]) / days
               - .005 * (delay if delay is not None else 3600.))
    return (safety(result), budget(result, settings), -max(0., rate - settings.false_budget_per_day),
            -max(0., workload - settings.workload_budget), utility, -rate, -workload)
