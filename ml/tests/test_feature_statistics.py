import numpy as np
import pytest

from ultron_health_ml.features.statistics import changes, correlation, describe, historical_baseline, movement


def test_rolling_stats_use_population_std():
    result = describe(np.array([10.0, 20.0, 30.0]))
    assert result == pytest.approx({"mean": 20, "median": 20, "std": np.sqrt(200 / 3), "min": 10, "max": 30})


def test_mad_robust_deviation_and_zero_mad():
    result = historical_baseline(np.array([10.0, 20.0, 30.0]), 50, 1e-8)
    assert result == pytest.approx({"baseline_median": 20, "baseline_mad": 10, "baseline_deviation": 30,
                                   "robust_z": 30 / (1.4826 * 10 + 1e-8)})
    result = historical_baseline(np.array([20.0, 20.0, 20.0]), 50, 1e-8)
    assert result["baseline_mad"] == 0 and result["robust_z"] is None
    assert result["baseline_deviation"] == 30


@pytest.mark.parametrize("initial", [0.0, 1e-9, -1e-9])
def test_no_percentage_change_for_near_zero_denominator(initial):
    result = changes(np.array([0, 30_000_000, 75_000_000]), np.array([initial, 10.0, 20.0]), 1e-8, 60)
    assert result["pct_change"] is None
    assert result["abs_change"] == pytest.approx(20)


@pytest.mark.parametrize("times", [[0, 0], [1, 0]])
def test_nonpositive_duration_has_no_rate(times):
    assert not changes(np.array(times), np.array([1.0, 2.0]), 1e-8, 60)


def test_short_or_constant_trend_does_not_invent_quality():
    times = np.array([0, 15_000_000, 30_000_000])
    values = np.array([25.0, 25.0, 25.0])
    assert changes(times, values, 1e-8, 60)["slope_per_hour"] is None
    result = changes(times, values, 1e-8, 30)
    assert result["slope_per_hour"] == 0 and result["trend_r2"] is None
    assert correlation(times, values, 1e-8) is None


def test_fractional_constant_has_exactly_zero_variance_and_slope():
    values = np.full(11, 0.1)
    times = np.arange(11) * 30_000_000
    assert describe(values)["std"] == 0
    assert describe(values)["mean"] == 0.1
    assert changes(times, values, 1e-8, 60)["slope_per_hour"] == 0


@pytest.mark.parametrize("values,expected", [
    ([1], (None, None)), ([1, 2, 2], (0, 0)), ([1, 3, 2, 1], (0, 2)), ([5, 2, 3, 4], (2, 0)),
])
def test_movement_uses_latest_direction_and_stops_at_ties(values, expected):
    assert movement(values) == expected
