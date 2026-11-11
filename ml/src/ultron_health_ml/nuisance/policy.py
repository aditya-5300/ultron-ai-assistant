"""B advisory scoring wrapped around the unchanged A deterministic safety pass."""

from dataclasses import dataclass

from ultron_health_ml.hybrid.config import Settings
from ultron_health_ml.hybrid.detector import Replay
from ultron_health_ml.hybrid.signals import signals
from ultron_health_ml.nuisance.protocol import POLICIES


@dataclass
class Observation:
    subsystem: str
    opened_at: float
    last_evidence_at: float
    closed_at: float | None = None


def lifecycle(config, flags):
    base = config.model_dump()
    for key, spec in base["subsystems"].items():
        if key == "disk_capacity":
            continue
        if "lifecycle" in flags:
            spec["min_duration"] = max(spec["min_duration"], 300.)
            spec["recovery_seconds"] = min(spec["recovery_seconds"], 150.)
        if key == "process" and "process" in flags:
            spec["min_duration"] = 330.
            spec["min_observations"] = 6
        if key == "memory" and "memory" in flags:
            spec["min_duration"] = 240.
    if "scope" in flags or "process" in flags or "fusion" in flags:
        modes = base["subsystems"]["process"]["modes"]
        base["subsystems"]["process"]["modes"] = (*modes, "sustained")
    if "scope" in flags:
        base["subsystems"]["events"]["modes"] = ("sustained",)
    return Settings.model_validate(base)


class PolicyReplay(Replay):
    def __init__(self, config, name, multiplier=2.):
        self.policy = name
        self.options = POLICIES[name]
        mode = name if name in {"fixed_budget", "mad_budget"} else "sustained"
        super().__init__(lifecycle(config, self.options), mode, multiplier, scorer=self.score)
        self.silent = []
        self.observing = {}
        self.associations = 0
        self._weak = {}
        self._family = {}

    def score(self, row, spec, state, config, value, observed, fresh):
        computed = signals(row, spec, state, config, value, observed, fresh)
        if not self.options or self.policy == "lifecycle":
            return computed
        values = row.values
        metric = spec.metric
        key = next(key for key, item in config.subsystems.items() if item.metric == metric)
        cpu = values.get("cpu_usage_pct__current")
        memory = values.get("memory_usage_pct__current")
        processes = values.get("selected_process_nonresponding_count__current")
        high_load = cpu is not None and cpu >= 76
        sustained = computed.get("sustained", 0.)
        family_scores = {"sustained": sustained, **{family: computed.get(family, 0.)
                         for family in ("mad", "ewma", "cusum") if family in self.options and family in spec.modes}}
        family = max(family_scores, key=family_scores.get)
        score = family_scores[family]
        if key == "memory" and "memory" in self.options:
            delta = values.get(f"{metric}__delta_300s")
            slope = values.get(f"{metric}__slope_per_hour_300s")
            fit = values.get(f"{metric}__trend_r2_300s")
            supporting = processes is not None and processes >= 3
            level = 83 if high_load and not supporting else 74
            minimum_delta = 5 if high_load and not supporting else 1.6
            qualified = (value >= level and delta is not None and delta >= minimum_delta
                         and slope is not None and slope > 0 and fit is not None and fit >= .55)
            score = score if qualified else 0.
        if key == "cpu" and "scope" in self.options:
            corroborated = ((processes is not None and processes >= 3)
                            or (memory is not None and memory >= 90))
            score = max(score, self.multiplier) if value >= 86 and corroborated else 0.
        if key == "process" and ("process" in self.options or "fusion" in self.options):
            count_limit = 5 if high_load else 4
            event_count = values.get("relevant_event_count__current")
            corroborated = (memory is not None and memory >= 85) or (event_count is not None and event_count >= 4)
            score = self.multiplier if value >= count_limit or (value >= 3 and corroborated) else 0.
            if "fusion" in self.options and high_load and value < 7:
                score = 0.
        if key == "events" and "scope" in self.options:
            score = self.multiplier if value >= 8 else 0.
        weak = score
        if key in {"battery_drain", "disk_health"} and ("scope" in self.options or
                                                        key == "battery_drain" and "no_drain" in self.options):
            score = 0.
        if fresh:
            self._weak[key] = (weak >= .65 * self.multiplier, observed)
            if score >= self.multiplier:
                self._family[key] = family
        return {"sustained": score}

    def step(self, row):
        self._weak.clear()
        self._family.clear()
        availability, flags = super().step(row)
        now = row.sample_time.timestamp()
        for key, (active, observed) in self._weak.items():
            if active and key not in self.open:
                if key not in self.observing:
                    notice = Observation(key, now, observed)
                    self.silent.append(notice)
                    self.observing[key] = notice
                self.observing[key].last_evidence_at = observed
            elif key in self.observing:
                self.observing.pop(key).closed_at = now
        for key in tuple(self.observing):
            if key in self.open:
                self.observing.pop(key).closed_at = now
        for key, family in self._family.items():
            if key in flags and key in self.open and self.open[key].last_confirmed_at == now:
                self.open[key].detector_sources.add(f"b_policy_{family}")
        if "fusion" in self.options and (episode := self.open.get("cpu")) is not None:
            values = row.values
            cpu = values.get("cpu_usage_pct__current")
            count = values.get("selected_process_nonresponding_count__current")
            if (cpu is not None and cpu >= 76 and count is not None and count >= 2
                    and values.get("selected_process_nonresponding_count__fresh") == 1):
                if "associated_process_nonresponse" not in episode.detector_sources:
                    self.associations += 1
                episode.detector_sources.add("associated_process_nonresponse")
                episode.evidence["supporting_signal"] = "coincident process nonresponse; cause unknown"
        return availability, flags
