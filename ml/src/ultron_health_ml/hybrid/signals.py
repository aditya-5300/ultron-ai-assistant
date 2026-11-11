"""Causal subsystem signals. Cached readings never update statistical state."""

from dataclasses import dataclass
from math import expm1

from ultron_health_ml.hybrid.config import Settings, Subsystem


@dataclass
class ShiftState:
    anchor: float | None = None
    smooth: float | None = None
    cusum: float = 0.
    observed: float | None = None
    samples: int = 0

    def reset(self):
        self.anchor = self.smooth = self.observed = None
        self.cusum = 0.
        self.samples = 0

    def update(self, value: float, time: float, spec: Subsystem, config: Settings):
        if self.observed is not None and time <= self.observed:
            return self.read(spec)
        elapsed = time - self.observed if self.observed is not None else 0.
        if self.anchor is None or elapsed > spec.max_observation_gap:
            self.reset()
            self.anchor = self.smooth = value
        else:
            self.smooth += -expm1(-elapsed / config.ewma_seconds) * (value - self.smooth)
            error = value - self.anchor
            self.cusum = min(4 * spec.cusum_limit, max(0., self.cusum + (
                error / spec.noise_floor - spec.cusum_drift
            ) * min(2., elapsed / 30.)))
            if error <= spec.ewma_shift and self.cusum < spec.cusum_limit:
                self.anchor += -expm1(-elapsed / config.reference_seconds) * error
        self.observed = time
        self.samples += 1
        return self.read(spec)

    def read(self, spec):
        if self.samples < spec.mad_history_points:
            return {}
        return {"ewma": max(0., self.smooth - self.anchor) / spec.ewma_shift,
                "cusum": self.cusum / spec.cusum_limit}


def signals(row, spec, state, config, value, observed, fresh):
    metric = spec.metric
    values = row.values
    sign = -1 if spec.direction == "low" else 1
    scores = {}
    z = values.get(f"{metric}__robust_z")
    deviation = values.get(f"{metric}__baseline_deviation")
    baseline = row.window_quality.get(f"{metric}__baseline_3600s")
    direction = abs if spec.direction == "absolute" else lambda number: sign * number
    if (z is not None and deviation is not None and baseline and baseline.valid
            and baseline.observation_count >= spec.mad_history_points):
        scores["mad"] = max(0., min(direction(z) / spec.mad_z,
                                   direction(deviation) / spec.noise_floor))
    window = spec.trend_window_seconds
    quality = row.window_quality.get(f"{metric}__window_{window}s")
    slope = values.get(f"{metric}__slope_per_hour_{window}s")
    delta = values.get(f"{metric}__delta_{window}s")
    if (quality and quality.valid and quality.observation_count >= spec.min_observations
            and slope is not None and delta is not None):
        scores["sustained"] = max(0., min(direction(slope) / spec.minimum_slope_per_hour,
                                          direction(delta) / spec.minimum_delta))
    signed = abs(value - spec.attention_level) if spec.direction == "absolute" else sign * value
    scores.update(state.update(signed, observed, spec, config) if fresh else state.read(spec))
    return scores
