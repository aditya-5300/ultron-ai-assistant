"""Numeric operations use only the explicit window passed by the caller."""

from math import isfinite, sqrt

import numpy as np

from ultron_health_ml.features.windows import MICROSECONDS


def _median(values: np.ndarray) -> float:
    # Windows already contain finite samples; partition finds their middle without sorting all of them.
    middle = len(values) // 2
    if len(values) % 2:
        return float(np.partition(values, middle)[middle])
    middle_values = np.partition(values, (middle - 1, middle))
    return float(middle_values[middle - 1]) / 2 + float(middle_values[middle]) / 2


def describe(values: np.ndarray) -> dict[str, float]:
    lower, upper = float(values.min()), float(values.max())
    if lower == upper:
        return {"mean": lower, "median": lower, "std": 0.0, "min": lower, "max": upper}
    mean = float(values.sum()) / len(values)
    centered = values - mean
    return {"mean": mean, "median": _median(values), "std": sqrt(float(centered @ centered) / len(values)),
            "min": lower, "max": upper}


def historical_baseline(values: np.ndarray, current: float, epsilon: float) -> dict[str, float | None]:
    median = _median(values)
    mad = _median(np.abs(values - median))
    deviation = current - median
    return {"baseline_median": median, "baseline_mad": mad, "baseline_deviation": deviation,
            "robust_z": deviation / (1.4826 * mad + epsilon) if mad > epsilon else None}


def changes(times: np.ndarray, values: np.ndarray, epsilon: float,
            minimum_trend_seconds: int, *, mean: float | None = None) -> dict[str, float | None]:
    elapsed = (int(times[-1]) - int(times[0])) / MICROSECONDS
    if elapsed <= 0:
        return {}
    first, last = float(values[0]), float(values[-1])
    delta = last - first
    result = {"delta": delta, "abs_change": abs(delta),
              "pct_change": 100 * delta / abs(first) if abs(first) > epsilon else None,
              "rate_per_hour": delta / elapsed * 3600, "slope_per_hour": None, "trend_r2": None}
    if len(values) >= 3 and elapsed >= minimum_trend_seconds:
        span = np.ptp(values)
        if span == 0:
            result["slope_per_hour"] = 0.0
            return result
        hours = (times - times[0]) / (3600 * MICROSECONDS)
        centered_time = hours - float(hours.sum()) / len(hours)
        centered_value = values - (float(values.sum()) / len(values) if mean is None else mean)
        variance = float(centered_time @ centered_time)
        if variance > 0:
            covariance = float(centered_time @ centered_value)
            result["slope_per_hour"] = covariance / variance
            coefficient = (_coefficient(covariance, variance, float(centered_value @ centered_value))
                           if elapsed / 3600 > epsilon and span > epsilon else None)
            result["trend_r2"] = coefficient ** 2 if coefficient is not None else None
    return result


def correlation(left: np.ndarray, right: np.ndarray, epsilon: float) -> float | None:
    if np.ptp(left) <= epsilon or np.ptp(right) <= epsilon:
        return None
    left = left - float(left.sum()) / len(left)
    right = right - float(right.sum()) / len(right)
    return _coefficient(float(left @ right), float(left @ left), float(right @ right))


def _coefficient(covariance: float, left_variance: float, right_variance: float) -> float | None:
    if not left_variance or not right_variance or not isfinite(left_variance) or not isfinite(right_variance):
        return None
    value = covariance / sqrt(left_variance) / sqrt(right_variance)
    return max(-1.0, min(1.0, value)) if isfinite(value) else None


def movement(values: list[float]) -> tuple[int | None, int | None]:
    if len(values) < 2:
        return None, None
    direction = (values[-1] > values[-2]) - (values[-1] < values[-2])
    if direction == 0:
        return 0, 0
    count = 1
    for index in range(len(values) - 2, 0, -1):
        if (values[index] - values[index - 1]) * direction <= 0:
            break
        count += 1
    return (count, 0) if direction > 0 else (0, count)
