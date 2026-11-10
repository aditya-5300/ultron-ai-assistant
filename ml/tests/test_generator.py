import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys

import pandas as pd
import pytest
import yaml
from pydantic import ValidationError

from ultron_health_ml.contracts.telemetry import DEEP_METRICS, METRIC_SPECS, TelemetryWindow
from ultron_health_ml.data_generation.config import GeneratorConfig, QualityEvent, load_config
from ultron_health_ml.data_generation.generator import generate, telemetry_frame, write_dataset


def config(**overrides):
    return GeneratorConfig(**(dict(days=0.05, scenarios=(), missing_probability=0) | overrides))


def test_deterministic_same_seed_and_different_seed():
    first, second = generate(config()), generate(config())
    assert first.window.model_dump_json() == second.window.model_dump_json()
    assert first.evaluation == second.evaluation
    different = generate(config(random_seed=43))
    assert [record.metrics["cpu_usage_pct"].value for record in first.window.records] != [
        record.metrics["cpu_usage_pct"].value for record in different.window.records
    ]


@pytest.mark.parametrize("interval", [15, 30, 60, 120, 300])
def test_interval_count_and_chronological_order(interval):
    settings = config(interval_seconds=interval)
    dataset = generate(settings)
    assert len(dataset.window.records) == math.ceil(settings.days * 86400 / interval)
    times = [record.sample_time for record in dataset.window.records]
    assert all((later - earlier).total_seconds() == interval for earlier, later in zip(times, times[1:]))
    assert TelemetryWindow.model_validate_json(dataset.window.model_dump_json())


def test_jitter_skips_and_overrun():
    regular = generate(config())
    jittered = generate(config(jitter_seconds=2))
    gaps = [record.elapsed_seconds for record in jittered.window.records[1:]]
    assert len(set(gaps)) > 2 and all(28 <= gap <= 32 for gap in gaps)
    skipped = generate(config(skip_probability=0.4))
    assert len(skipped.window.records) < len(regular.window.records)
    assert any(record.elapsed_seconds > 30 for record in skipped.window.records[1:])
    overrun = generate(config(overrun_probability=1, overrun_seconds=45))
    assert all(record.elapsed_seconds == 75 for record in overrun.window.records[1:])
    assert len(overrun.window.records) < len(regular.window.records)


def test_missing_sensor_and_coverage():
    event = QualityEvent(metric="cpu_usage_pct", kind="MISSING_SENSOR", start_fraction=0.2, duration_fraction=0.2)
    dataset = generate(config(quality_events=(event,)))
    for record in dataset.window.records:
        fraction = (record.sample_time - dataset.window.window_start).total_seconds() / (0.05 * 86400)
        reading = record.metrics["cpu_usage_pct"]
        if 0.2 <= fraction < 0.4:
            assert reading.value is None and reading.quality == "missing" and reading.availability == "unavailable"
        else:
            assert reading.value is not None and reading.quality == "fresh"


def test_missingness_does_not_change_latent_physics():
    regular = generate(config())
    missing = generate(config(missing_probability=0.5))
    for before, after in zip(regular.window.records, missing.window.records):
        assert before.sample_time == after.sample_time
        for name in set(METRIC_SPECS) - DEEP_METRICS:
            if after.metrics[name].value is not None:
                assert before.metrics[name].value == after.metrics[name].value


def test_cached_and_stale_deep_observation_times():
    event = QualityEvent(metric="disk_free_pct", kind="DEEP_COLLECTION_OUTAGE", start_fraction=0.1, duration_fraction=0.7)
    dataset = generate(config(quality_events=(event,)))
    readings = [(record, record.metrics["disk_free_pct"]) for record in dataset.window.records]
    assert {reading.quality for _, reading in readings} == {"fresh", "cached", "stale"}
    for record, reading in readings:
        if reading.quality == "fresh":
            assert reading.observation_time == record.sample_time
        else:
            assert reading.observation_time < record.sample_time
        if reading.quality == "stale":
            assert reading.usable_value() is None
    assert readings[-1][1].quality in {"fresh", "cached"}


def test_no_zero_filling_before_first_deep_observation():
    event = QualityEvent(metric="disk_free_pct", kind="DEEP_COLLECTION_OUTAGE", start_fraction=0, duration_fraction=0.5)
    dataset = generate(config(quality_events=(event,)))
    assert dataset.window.records[0].metrics["disk_free_pct"].value is None
    assert dataset.window.records[0].metrics["disk_free_pct"].quality == "missing"


def test_metric_bounds_and_slow_normal_memory_disk():
    dataset = generate(config(days=1))
    frame = telemetry_frame(dataset)
    for name, (lower, upper, *_) in METRIC_SPECS.items():
        assert frame[name].min() >= lower
        if upper is not None:
            assert frame[name].max() <= upper
    assert frame.memory_usage_pct.diff().abs().quantile(0.95) < frame.cpu_usage_pct.diff().abs().quantile(0.95)
    assert frame.disk_free_pct.max() - frame.disk_free_pct.min() < 1
    assert frame.network_latency_ms.autocorr() > 0.5
    assert frame.cpu_usage_pct.corr(frame.throughput_rps) > 0.7


def test_outputs_separate_labels_manifest_and_refuse_overwrite(tmp_path):
    dataset = generate(config())
    write_dataset(dataset, tmp_path)
    frame = pd.read_csv(tmp_path / "telemetry.csv")
    assert not {"scenario", "is_anomaly", "incident_time", "severity"} & set(frame.columns)
    evaluation = json.loads((tmp_path / "scenarios.json").read_text())
    assert len(evaluation["observations"]) == len(frame)
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    for name, checksum in manifest["sha256"].items():
        assert hashlib.sha256((tmp_path / name).read_bytes()).hexdigest() == checksum
    with pytest.raises(ValueError, match="empty"):
        write_dataset(dataset, tmp_path)


def test_null_csv_values_remain_null(tmp_path):
    event = QualityEvent(metric="cpu_usage_pct", kind="MISSING_SENSOR", start_fraction=0, duration_fraction=1)
    write_dataset(generate(config(quality_events=(event,))), tmp_path)
    frame = pd.read_csv(tmp_path / "telemetry.csv")
    assert frame.cpu_usage_pct.isna().all()


def test_export_bytes_reproducible(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    write_dataset(generate(config()), first)
    write_dataset(generate(config()), second)
    for name in ("telemetry.csv", "scenarios.json", "manifest.json"):
        assert (first / name).read_bytes() == (second / name).read_bytes()


@pytest.mark.parametrize("overrides", [dict(interval_seconds=10), dict(days=-1), dict(jitter_seconds=16),
                                      dict(skip_probability=1), dict(days=365, interval_seconds=15),
                                      dict(deep_stale_after_seconds=100)])
def test_invalid_settings_rejected(overrides):
    with pytest.raises(ValidationError):
        config(**overrides)


def test_config_file_and_cli_overrides(tmp_path):
    path = Path(__file__).parents[1] / "configs" / "synthetic.yaml"
    settings = load_config(path, days=0.02, interval_seconds=60)
    assert settings.days == 0.02 and settings.interval_seconds == 60
    assert len(settings.scenarios) == 12
    command = [sys.executable, "-m", "ultron_health_ml.data_generation.generator", "--config", str(path),
               "--days", "0.02", "--interval", "60", "--seed", "7", "--output", str(tmp_path / "output")]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "output" / "telemetry.csv").exists()
    invalid = subprocess.run(command + ["--days", "-1"], capture_output=True, text=True)
    assert invalid.returncode == 2 and "Generator error" in invalid.stderr


def test_yaml_rejects_unknown_and_unsafe_input(tmp_path):
    for value in ["generator: {unknown_setting: 1}", "unknown: 5", "generator: []", "!!python/object:os.system {}"]:
        file = tmp_path / "bad.yaml"
        file.write_text(value)
        with pytest.raises((ValueError, yaml.YAMLError)):
            load_config(file)


@pytest.mark.parametrize("setting", [
    dict(metric="unknown", kind="MISSING_SENSOR", start_fraction=0, duration_fraction=0.1),
    dict(metric="cpu_usage_pct", kind="DEEP_COLLECTION_OUTAGE", start_fraction=0, duration_fraction=0.1),
    dict(metric="cpu_usage_pct", kind="MISSING_SENSOR", start_fraction=0.9, duration_fraction=0.2),
])
def test_invalid_quality_events_rejected(setting):
    with pytest.raises(ValidationError):
        QualityEvent(**setting)
