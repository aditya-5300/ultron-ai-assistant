"""Causal, interpretable forecasting baselines; no fitted classifier."""

from collections import deque
from dataclasses import dataclass
from statistics import median

from ultron_health_ml.features.config import FeatureConfig
from ultron_health_ml.forecast.contract import METRICS, SYSTEMS, cpu_context

FEATURES = FeatureConfig(metrics=METRICS)
METHODS = ("persistence", "linear", "robust", "eta")
FLOOR = {"cpu": .5, "disk_capacity": .12, "process": .15}


@dataclass(frozen=True)
class Signal:
    score: float
    eta_seconds: float | None = None
    sufficient: bool = True


UNKNOWN = Signal(0., None, False)


def fit_noise(development, targets):
    """Only preselection development telemetry can fit the robust noise floor."""
    differences = {key: [] for key in SYSTEMS}
    for run, source in development:
        if run["split"] != "development":
            raise ValueError("noise preprocessing accepts development telemetry only")
        previous = {}
        for record in source.window.records:
            for key in SYSTEMS:
                reading = record.metrics[targets.targets[key].metric]
                if reading.quality != "fresh":
                    continue
                if key in previous:
                    differences[key].append(abs(reading.value - previous[key]))
                previous[key] = reading.value
    if any(not differences[key] for key in SYSTEMS):
        raise ValueError("development runs lack fresh differences")
    return {key: max(FLOOR[key], median(differences[key])) for key in SYSTEMS}


class History:
    def __init__(self, key):
        self.points = deque()
        self.window = 5400 if key == "disk_capacity" else 900
        self.minimum_span = 1200 if key == "disk_capacity" else 240
        self.cached_fit = None

    def add(self, observed, value, gap):
        if self.points and observed <= self.points[-1][0]:
            return
        if self.points and observed - self.points[-1][0] > gap:
            self.points.clear()
        self.points.append((observed, value))
        while self.points and self.points[0][0] < observed - self.window:
            self.points.popleft()
        self.cached_fit = self.fit()

    def fit(self):
        if len(self.points) < 5 or self.points[-1][0] - self.points[0][0] < self.minimum_span:
            return None
        points = list(self.points)
        offsets = [stamp - points[0][0] for stamp, _ in points]
        values = [value for _, value in points]
        mean_offset, mean_value = sum(offsets) / len(points), sum(values) / len(points)
        spread = sum((stamp - mean_offset) ** 2 for stamp in offsets)
        variation = sum((value - mean_value) ** 2 for value in values)
        if not spread or not variation:
            return 0., 0., 0., 0., offsets[-1]
        cross = sum((stamp - mean_offset) * (value - mean_value) for stamp, value in zip(offsets, values))
        slope = cross / spread
        quality = min(1., cross ** 2 / (spread * variation))
        sample = points[::max(1, len(points) // 9)]
        if sample[-1] != points[-1]:
            sample.append(points[-1])
        slopes = [(right[1] - left[1]) / (right[0] - left[0])
                  for index, left in enumerate(sample) for right in sample[index + 1:]
                  if right[0] - left[0] >= self.minimum_span / 4]
        robust = median(slopes) if slopes else 0.
        agreement = sum(value * robust > 0 for value in slopes) / len(slopes) if slopes else 0.
        return slope, quality, robust, agreement, offsets[-1]


def measurement(record, feature_row, target):
    reading = record.metrics.get(target.metric)
    if reading is None or feature_row.values.get(f"{target.metric}__current") is None:
        return None, False
    return reading.observation_time.timestamp(), reading.quality == "fresh"


def scores(key, target, values, tracker, history, noise):
    value = values.get(target.metric)
    if value is None or (key == "cpu" and cpu_context(values) is None):
        return dict.fromkeys(METHODS, UNKNOWN)
    if tracker.active:
        return dict.fromkeys(METHODS, Signal(0.))
    if key == "cpu" and cpu_context(values) is not True:
        return dict.fromkeys(METHODS, Signal(0.))
    if key == "disk_capacity":
        persistence = Signal(.90 if target.boundary < value <= 13.5 else 0.)
    else:
        count = len(history.points)
        near = 77 if key == "cpu" else 2
        rate = sum(observed >= near for _, observed in list(history.points)[-10:]) / min(10, count) if count else 0.
        persistence = Signal(rate if count >= 5 and value >= near else 0.,
                             max(1., target.hold_seconds - tracker.entry.duration), count >= 5)
    output = {"persistence": persistence, "linear": UNKNOWN, "robust": UNKNOWN, "eta": UNKNOWN}
    fit = history.cached_fit
    if fit is None:
        return output
    slope, quality, robust, agreement, span = fit
    sign = -1 if target.direction == "low" else 1
    remaining = sign * (target.boundary - value)
    minimum_change = max(noise * 2, 1. if key == "disk_capacity" else .5)

    def project(rate, confidence, floor):
        if remaining <= 0 or sign * rate * span < minimum_change or confidence < floor:
            return Signal(0.)
        eta = remaining / (sign * rate) + target.hold_seconds
        return Signal(confidence, eta)

    output["linear"] = project(slope, quality, .30)
    output["robust"] = project(robust, agreement, .55)
    if key == "disk_capacity":
        output["eta"] = project(slope, quality, .75)
    return output
