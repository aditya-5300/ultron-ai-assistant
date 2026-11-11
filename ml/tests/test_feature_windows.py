import pytest

from feature_helpers import telemetry
from ultron_health_ml.features.config import FeatureConfig
from ultron_health_ml.features.engineering import iter_features


@pytest.mark.parametrize("interval,offsets", [
    (15, [0, 15, 30, 45, 60, 75, 90, 105, 120]),
    (30, [0, 30, 60, 90, 120]),
    (60, [0, 60, 120]),
    (30, [0, 31.7, 60.1, 89.3, 119.8]),
    (30, [0, 30, 90, 120]),
    (30, [0, 30, 104, 134]),
])
def test_time_normalized_rates_under_irregular_sampling(interval, offsets):
    window = telemetry([(offset, {"cpu_usage_pct": 10 + offset / 12}) for offset in offsets], interval=interval)
    config = FeatureConfig(metrics=("cpu_usage_pct",), windows_seconds=(120,))
    row = list(iter_features(window, config))[-1]
    fields = row.model_features()
    assert fields["cpu_usage_pct__rate_per_hour_120s"] == pytest.approx(300)
    assert fields["cpu_usage_pct__slope_per_hour_120s"] == pytest.approx(300)
    assert fields["cpu_usage_pct__trend_r2_120s"] == pytest.approx(1)
    assert not row.sampling_gap


def test_window_includes_exact_left_edge_and_excludes_older_readings():
    window = telemetry([(0, {"cpu_usage_pct": 100}), *[(time, {"cpu_usage_pct": 20}) for time in (30, 60, 90, 120, 150)]])
    config = FeatureConfig(metrics=("cpu_usage_pct",), windows_seconds=(120,))
    rows = list(iter_features(window, config))
    assert rows[-2].model_features()["cpu_usage_pct__mean_120s"] == 36
    assert rows[-1].model_features()["cpu_usage_pct__mean_120s"] == 20
    assert rows[-1].window_quality["cpu_usage_pct__window_120s"].observation_count == 5


def test_large_gap_discards_old_evidence_even_inside_window():
    window = telemetry([(time, {"cpu_usage_pct": time / 10}) for time in (0, 30, 60, 190, 220, 250)])
    config = FeatureConfig(metrics=("cpu_usage_pct",), windows_seconds=(300,))
    rows = list(iter_features(window, config))
    assert rows[3].sampling_gap
    assert rows[3].window_quality["cpu_usage_pct__window_300s"].observation_count == 1
    assert rows[-1].model_features()["cpu_usage_pct__slope_per_hour_300s"] is None
    assert rows[-1].window_quality["cpu_usage_pct__window_300s"].largest_gap_seconds == 30


def test_cached_deep_samples_are_not_independent_observations():
    disk = "disk_free_pct"
    observations = [(offset, {disk: 67 if offset == 0 else (67, 0, "cached")})
                    for offset in range(0, 300, 30)]
    observations += [(300, {disk: 63})]
    observations += [(offset, {disk: (63, 300, "cached")}) for offset in range(330, 600, 30)]
    observations += [(600, {disk: 59}), (630, {disk: (59, 600, "cached")})]
    config = FeatureConfig(metrics=(disk,), windows_seconds=(300, 900))
    rows = list(iter_features(telemetry(observations), config))
    assert rows[9].window_quality["disk_free_pct__window_300s"].observation_count == 1
    assert sum(len(row.cached_duplicates) for row in rows[:10]) == 9
    assert rows[9].model_features()["disk_free_pct__std_300s"] is None
    assert rows[-1].window_quality["disk_free_pct__window_900s"].observation_count == 3
    assert rows[-1].model_features()["disk_free_pct__mean_900s"] == 63
    assert rows[-1].model_features()["disk_free_pct__consecutive_decreases"] == 2
    assert rows[-1].model_features()["disk_free_pct__observation_age_seconds"] == 30


def test_cache_cannot_restore_history_after_discontinuity():
    disk = "disk_free_pct"
    window = telemetry([(0, {disk: 67}), (30, {disk: (67, 0, "cached")}), (150, {disk: (67, 0, "cached")})])
    row = list(iter_features(window, FeatureConfig(metrics=(disk,))))[-1]
    assert row.sampling_gap
    assert row.model_features()["disk_free_pct__current"] == 67
    assert row.window_quality["disk_free_pct__window_120s"].observation_count == 0


def test_cached_statistics_expire_when_window_membership_changes():
    disk = "disk_free_pct"
    samples = []
    for offset in range(0, 931, 30):
        observed = min(offset // 300 * 300, 600)
        value = 67 - 4 * (observed // 300)
        samples.append((offset, {disk: value if observed == offset else (value, observed, "cached")}))
    rows = list(iter_features(telemetry(samples), FeatureConfig(metrics=(disk,), windows_seconds=(900,))))
    assert rows[-2].model_features()[f"{disk}__mean_900s"] == 63
    assert rows[-1].model_features()[f"{disk}__mean_900s"] is None
    assert rows[-1].window_quality[f"{disk}__window_900s"].observation_count == 2


def test_conflicting_repeat_is_rejected():
    window = telemetry([(0, {"disk_free_pct": 67}), (30, {"disk_free_pct": (68, 0, "cached")})])
    with pytest.raises(ValueError, match="inconsistent"):
        list(iter_features(window, FeatureConfig()))


def test_ac_change_resets_power_sensitive_histories_only():
    window = telemetry([(time, {"cpu_usage_pct": 30 + time / 10, "memory_usage_pct": 65, "battery_pct": 80})
                        for time in (0, 30, 60, 90)], contexts={90: (False, "saver")})
    row = list(iter_features(window, FeatureConfig()))[-1]
    assert row.context_changed
    assert row.window_quality["cpu_usage_pct__window_120s"].observation_count == 1
    assert row.window_quality["battery_pct__window_120s"].observation_count == 1
    assert row.window_quality["memory_usage_pct__window_120s"].observation_count == 4
