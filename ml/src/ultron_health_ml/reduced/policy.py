"""Common advisory lifecycle with independent deterministic safety."""

from dataclasses import dataclass
from math import ceil

from ultron_health_ml.hybrid.config import Settings
from ultron_health_ml.hybrid.detector import Replay
from ultron_health_ml.reduced.protocol import SCOPE

FLOORS = {"cpu": 6., "disk_capacity": 1.5, "process": 1.}
ENTRY = {"cpu": (5, 210.), "disk_capacity": (3, 560.), "process": (6, 300.)}


@dataclass
class Observe:
    subsystem: str
    opened_at: float
    last_confirmed_at: float
    closed_at: float | None = None


def common_signals(row):
    values = row.values
    cpu = values.get("cpu_usage_pct__current")
    process = values.get("selected_process_nonresponding_count__current")
    ram = values.get("memory_usage_pct__current")
    disk = values.get("disk_free_pct__current")
    cpu_support = (process is not None and process >= 3) or (ram is not None and ram >= 88)
    ready = {
        "cpu": cpu is not None and cpu >= 78 and cpu_support,
        "process": process is not None and process >= 3 and (cpu is None or cpu < 76 or process >= 7),
        "disk_capacity": disk is not None and disk <= 28.,
    }
    output = {}
    for key, metric in (("cpu", "cpu_usage_pct"), ("process", "selected_process_nonresponding_count"),
                        ("disk_capacity", "disk_free_pct")):
        if not ready[key]:
            output[key] = {family: 0. for family in ("fixed", "mad", "sustained", "hybrid")}
            continue
        level = values[f"{metric}__current"]
        direction = -1 if key == "disk_capacity" else 1
        threshold_score = max(0., (1 + (20 - level) / 20) if key == "disk_capacity"
                              else level / (4. if key == "process" else 86.))
        baseline = row.window_quality.get(f"{metric}__baseline_3600s")
        deviation = values.get(f"{metric}__baseline_deviation")
        mad = values.get(f"{metric}__baseline_mad")
        robust = (max(0., direction * deviation / (4 * max(FLOORS[key], 1.4826 * mad)))
                  if baseline and baseline.valid and deviation is not None and mad is not None else 0.)
        window = 3600 if key == "disk_capacity" else 900
        observed = row.window_quality.get(f"{metric}__window_{window}s")
        slope = values.get(f"{metric}__slope_per_hour_{window}s")
        delta = values.get(f"{metric}__delta_{window}s")
        sustained = 0.
        if observed and observed.valid and observed.observation_count >= ENTRY[key][0]:
            if key == "process":
                short_mean = values.get(f"{metric}__mean_{window}s")
                if short_mean is not None:
                    sustained = max(0., short_mean / 3.)
            elif slope is not None and delta is not None:
                sustained = max(0., min(direction * slope / (7. if key == "disk_capacity" else 18.),
                                        direction * delta / (4. if key == "disk_capacity" else 10.)))
        hybrid = max(sustained, threshold_score)
        output[key] = dict(fixed=threshold_score, mad=robust, sustained=sustained, hybrid=hybrid)
    return output


def lifecycle(config, point):
    data = config.model_dump()
    for key, (count, seconds) in ENTRY.items():
        spec = data["subsystems"][key]
        spec.update(min_observations=ceil(count * min(point.persistence, 1.5)),
                    min_duration=seconds * point.persistence)
    return Settings.model_validate(data)


class ReducedReplay(Replay):
    def __init__(self, config, point):
        self.point = point
        self.current_scores = dict
        self.silent = []
        self.observing = {}
        self.keys = {spec.metric: key for key, spec in config.subsystems.items()}
        super().__init__(lifecycle(config, point), "mad_budget", point.level, scorer=self.score)

    def score(self, row, spec, *_):
        key = self.keys[spec.metric]
        return {"mad": self.current_scores().get(key, {}).get(self.point.method, 0.)}

    def consume(self, row, scores):
        self.current_scores = scores
        coverage, flags = self.step(row)
        signals = scores()
        now = row.sample_time.timestamp()
        for key in SCOPE:
            if row.values.get(f"{self.config.subsystems[key].metric}__fresh") != 1 or not coverage[key]:
                continue
            value = signals.get(key, {}).get(self.point.method, 0.)
            if key not in self.open and value >= .65 * self.point.level:
                if key not in self.observing:
                    notice = Observe(key, now, now)
                    self.silent.append(notice)
                    self.observing[key] = notice
                self.observing[key].last_confirmed_at = now
            elif key in self.observing:
                self.observing.pop(key).closed_at = now
        for key, episode in self.open.items():
            if key in self.observing:
                self.observing.pop(key).closed_at = now
            if key in flags and "mad" in episode.detector_sources:
                episode.detector_sources.discard("mad")
                episode.detector_sources.add(self.point.method)
                episode.evidence[self.point.method] = episode.evidence.pop("mad", 0.)
        cpu = self.open.get("cpu")
        if cpu and row.values.get("selected_process_nonresponding_count__fresh") == 1:
            count = row.values.get("selected_process_nonresponding_count__current")
            level = row.values.get("cpu_usage_pct__current")
            if count is not None and count >= 3 and level is not None and level >= 76:
                cpu.detector_sources.add("associated_process_nonresponse")
                cpu.evidence["supporting_signal"] = "coincident process nonresponse; cause unknown"
        return coverage, flags
