import csv
import json
from pathlib import Path
import subprocess
import sys

import pytest

from feature_helpers import telemetry
from ultron_health_ml.contracts.telemetry import REAL_ULTRON_METRICS, SYNTHETIC_APPLICATION_METRICS
from ultron_health_ml.data_generation.config import GeneratorConfig
from ultron_health_ml.data_generation.generator import generate, write_dataset
from ultron_health_ml.features.config import FeatureConfig
from ultron_health_ml.features.engineering import iter_features
from ultron_health_ml.features.export import write_features
from ultron_health_ml.features.schema import METADATA_COLUMNS, feature_inventory
from ultron_health_ml.features.validation import LoadedTelemetry


@pytest.mark.parametrize("name", ["cpu_usage_pct", "memory_usage_pct", "disk_free_pct", "battery_pct"])
def test_missing_reading_never_becomes_healthy_zero(name):
    window = telemetry([(0, {name: 60}), (30, {name: 65}), (60, {name: None}), (90, {name: 66})])
    config = FeatureConfig(metrics=(name,), windows_seconds=(120,))
    rows = list(iter_features(window, config))
    missing = rows[2].model_features()
    assert missing[f"{name}__current"] is None
    assert missing[f"{name}__available"] == 0
    assert missing[f"{name}__observation_age_seconds"] is None
    assert missing[f"{name}__mean_120s"] is None
    assert rows[2].continuity_breaks["unusable_metric"] == 1
    assert rows[-1].window_quality[f"{name}__window_120s"].observation_count == 1


def test_entire_missing_and_omitted_windows_remain_invalid():
    window = telemetry([(time, {"battery_pct": None}) for time in (0, 30, 60, 90, 120)])
    result = list(iter_features(window, FeatureConfig()))[-1]
    for name in REAL_ULTRON_METRICS:
        assert result.model_features()[f"{name}__current"] is None
        assert result.window_quality[f"{name}__window_120s"].observation_count == 0
        assert not result.window_quality[f"{name}__window_120s"].valid


@pytest.mark.parametrize("quality", ["cached", "stale"])
def test_stale_disk_by_quality_or_age(quality):
    disk = "disk_free_pct"
    window = telemetry([(0, {disk: 50}), (30, {disk: (50, 0, "cached")}), (60, {disk: (50, 0, quality)})])
    row = list(iter_features(window, FeatureConfig(metrics=(disk,), max_cached_age_seconds=60)))[-1]
    values = row.model_features()
    assert values[f"{disk}__current"] is None
    assert values[f"{disk}__cached"] == 0 and values[f"{disk}__stale"] == 1
    assert values[f"{disk}__available"] == 1 and values[f"{disk}__observation_age_seconds"] == 60
    assert not row.window_quality[f"{disk}__window_120s"].valid


def test_threshold_margins_and_concurrent_pressure():
    record = telemetry([(0, {"cpu_usage_pct": 75, "memory_usage_pct": 94, "disk_free_pct": 7})], source="ultron")
    row = next(iter_features(record, FeatureConfig()))
    assert row.model_features()["memory_usage_pct__threshold_margin"] == -4
    assert row.model_features()["disk_free_pct__threshold_margin"] == -3
    assert row.model_features()["cpu_memory_pressure"] == 75


def test_profiles_exclude_hypothetical_inputs_from_real_profile():
    real, extended = FeatureConfig(), FeatureConfig(profile="synthetic_extended")
    real_names, extended_names = feature_inventory(real), feature_inventory(extended)
    for name in SYNTHETIC_APPLICATION_METRICS:
        assert f"{name}__current" not in real_names and f"{name}__current" in extended_names
    record = telemetry([(0, {"cpu_usage_pct": 10})], source="ultron")
    with pytest.raises(ValueError, match="requires synthetic"):
        list(iter_features(record, extended))


def test_synthetic_correlations_use_matched_fresh_pairs_and_reset_on_missing():
    metrics = [
        (time, {"cpu_usage_pct": 10 + index, "response_time_ms": 10 + index * 10,
                "network_latency_ms": 5 + index, "throughput_rps": 10 + index * 10, "error_rate": 0.3 - index * 0.02})
        for index, time in enumerate((0, 30, 60, 90, 120))
    ]
    metrics.append((150, {"cpu_usage_pct": None, "response_time_ms": 70, "network_latency_ms": 11,
                          "throughput_rps": 70, "error_rate": 0.15}))
    rows = list(iter_features(telemetry(metrics), FeatureConfig(profile="synthetic_extended", cross_window_seconds=120)))
    values = rows[-2].model_features()
    assert values["cpu_response_correlation_120s"] == pytest.approx(1)
    assert values["latency_response_correlation_120s"] == pytest.approx(1)
    assert values["throughput_error_correlation_120s"] == pytest.approx(-1)
    assert rows[-1].model_features()["cpu_response_correlation_120s"] is None


def test_export_reports_constant_near_constant_and_null_without_dropping(tmp_path):
    window = telemetry([(time, {"cpu_usage_pct": 30, "memory_usage_pct": 50 + index * 1e-9})
                        for index, time in enumerate((0, 30, 60, 90, 120))])
    config = FeatureConfig(metrics=("cpu_usage_pct", "memory_usage_pct", "disk_free_pct"))
    source = LoadedTelemetry(window, "fixture", "test-checksum")
    output = tmp_path / "result"
    manifest = write_features(source, config, iter_features(window, config, dataset_version="fixture"), output)
    report = manifest["quality"]
    assert report["raw_observations"] == report["feature_rows"] == 5
    assert "cpu_usage_pct__current" in report["constant_features"]
    assert "memory_usage_pct__current" in report["near_constant_features"]
    assert "disk_free_pct__current" in report["all_missing_features"]
    assert report["per_feature"]["disk_free_pct__current"]["missing_pct"] == 100
    assert report["invalid_windows"] > 0
    assert tuple(manifest["model_columns"]) == tuple(feature_inventory(config))
    with (output / "features.csv").open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        assert reader.fieldnames == [*METADATA_COLUMNS, *manifest["model_columns"]]
        assert next(reader)["disk_free_pct__current"] == ""
    with pytest.raises(ValueError, match="empty"):
        write_features(source, config, iter_features(window, config), output)


def test_cli_runs_without_scenario_file(tmp_path):
    source, output = tmp_path / "raw", tmp_path / "features"
    write_dataset(generate(GeneratorConfig(days=0.003, scenarios=())), source)
    (source / "scenarios.json").unlink()
    config = Path(__file__).parents[1] / "configs" / "features.yaml"
    result = subprocess.run([sys.executable, "-m", "ultron_health_ml.features.engineering", "--input", str(source),
                             "--config", str(config), "--profile", "real_ultron", "--output", str(output)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert (output / "features.csv").is_file()
    manifest = json.loads((output / "feature_manifest.json").read_text(encoding="utf-8"))
    assert manifest["feature_schema_version"] == manifest["telemetry_schema_version"] == "1.0.0"
    assert manifest["dataset_version"] == "0.1.0" and manifest["feature_profile"] == "real_ultron"
