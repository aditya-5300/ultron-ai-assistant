from dataclasses import replace
import hashlib
import json

import pytest

from feature_helpers import at, telemetry
from ultron_health_ml.data_generation.config import GeneratorConfig
from ultron_health_ml.data_generation.generator import generate, write_dataset
from ultron_health_ml.features.config import FeatureConfig
from ultron_health_ml.features.engineering import iter_features
from ultron_health_ml.features.schema import METADATA_COLUMNS
from ultron_health_ml.features.validation import load_evaluation_labels, load_telemetry


def features(window):
    config = FeatureConfig(metrics=("cpu_usage_pct",), windows_seconds=(120, 300),
                           baseline_seconds=120, baseline_min_observations=3)
    return list(iter_features(window, config))


def test_changing_every_future_observation_cannot_change_prefix():
    base = [(time, {"cpu_usage_pct": 10 + time / 10}) for time in range(0, 301, 30)]
    altered = [(time, values if time <= 150 else {"cpu_usage_pct": 100 - time / 15}) for time, values in base]
    assert features(telemetry(base))[:6] == features(telemetry(altered))[:6]


def test_appending_severe_future_anomaly_preserves_bounded_inference():
    prefix = [(time, {"cpu_usage_pct": 20 + time / 10}) for time in range(0, 151, 30)]
    full = features(telemetry(prefix + [(180, {"cpu_usage_pct": 100})]))
    inference = features(telemetry(prefix))
    assert full[:-1] == inference
    assert inference[-1].as_of == at(150)


def test_baseline_excludes_current_and_all_future_observations():
    rows = features(telemetry([(0, {"cpu_usage_pct": 10}), (30, {"cpu_usage_pct": 20}),
                              (60, {"cpu_usage_pct": 30}), (90, {"cpu_usage_pct": 99}),
                              (120, {"cpu_usage_pct": 100})]))
    result = rows[3].model_features()
    assert result["cpu_usage_pct__baseline_median"] == 20
    assert result["cpu_usage_pct__baseline_mad"] == 10
    assert result["cpu_usage_pct__baseline_deviation"] == 79
    assert rows[3].window_quality["cpu_usage_pct__baseline_120s"].observation_count == 3


def test_future_records_beyond_explicit_as_of_rejected():
    window = telemetry([(0, {"cpu_usage_pct": 10}), (30, {"cpu_usage_pct": 100})])
    unsafe = window.model_copy(update={"window_end": at(0), "as_of": at(0)})
    with pytest.raises(ValueError, match="outside bounded window/as_of"):
        features(unsafe)


def test_late_deep_observation_is_not_backfilled_before_acquisition():
    disk = "disk_free_pct"
    window = telemetry([(0, {disk: None}), (30, {disk: None}), (60, {disk: (60, 45, "cached")})])
    rows = list(iter_features(window, FeatureConfig(metrics=(disk,))))
    assert [row.model_features()[f"{disk}__current"] for row in rows] == [None, None, 60]
    assert rows[1].window_quality[f"{disk}__window_120s"].observation_count == 0


def test_cached_current_is_excluded_from_its_own_baseline():
    disk = "disk_free_pct"
    window = telemetry([(0, {disk: 40}), (300, {disk: 50}), (600, {disk: 60}),
                        (630, {disk: (60, 600, "cached")})], interval=300)
    config = FeatureConfig(metrics=(disk,), baseline_seconds=900, baseline_min_observations=2,
                           baseline_min_coverage=0.3)
    rows = list(iter_features(window, config))
    for row in rows[-2:]:
        assert row.model_features()[f"{disk}__baseline_median"] == 45
        assert row.window_quality[f"{disk}__baseline_900s"].observation_count == 2


def test_evaluation_label_changes_and_missing_labels_are_irrelevant(tmp_path):
    write_dataset(generate(GeneratorConfig(days=0.005, scenarios=(), missing_probability=0)), tmp_path)
    before = features(load_telemetry(tmp_path).window)
    labels_file = tmp_path / "scenarios.json"
    labels = json.loads(labels_file.read_text(encoding="utf-8"))
    for observation in labels["observations"][4:]:
        observation["is_anomaly"] = True
    labels_file.write_text(json.dumps(labels), encoding="utf-8")
    manifest_file = tmp_path / "manifest.json"
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    manifest["sha256"]["scenarios.json"] = hashlib.sha256(labels_file.read_bytes()).hexdigest()
    manifest_file.write_text(json.dumps(manifest), encoding="utf-8")
    assert any(row.is_anomaly for row in load_evaluation_labels(tmp_path).observations)
    assert features(load_telemetry(tmp_path).window) == before
    labels_file.unlink()
    assert features(load_telemetry(tmp_path).window) == before


def test_model_projection_is_allowlisted_even_if_values_contain_extra_keys():
    row = features(telemetry([(0, {"cpu_usage_pct": 25})]))[0]
    labels = {"scenario", "is_anomaly", "incident_id", "incident_time", "severity", "expected_behavior"}
    extras = labels | set(METADATA_COLUMNS)
    contaminated = replace(row, values=row.values | {name: 1.0 for name in extras})
    assert not extras.intersection(contaminated.model_features())
    assert contaminated.model_features() == row.model_features()
