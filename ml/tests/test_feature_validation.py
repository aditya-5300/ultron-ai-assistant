import csv
import hashlib
import json

import pytest
from pydantic import ValidationError

from ultron_health_ml.data_generation.config import GeneratorConfig
from ultron_health_ml.data_generation.generator import generate, write_dataset
from ultron_health_ml.features.config import FeatureConfig, Threshold, load_config
from ultron_health_ml.features.validation import load_evaluation_labels, load_telemetry


@pytest.fixture
def export(tmp_path):
    write_dataset(generate(GeneratorConfig(days=0.004, scenarios=(), missing_probability=0)), tmp_path)
    return tmp_path


def update_checksum(directory, name):
    path = directory / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["sha256"][name] = hashlib.sha256((directory / name).read_bytes()).hexdigest()
    path.write_text(json.dumps(manifest), encoding="utf-8")


def test_round_trip_preserves_quality_and_context(export):
    source = load_telemetry(export)
    first, second = source.window.records[:2]
    assert first.source == "synthetic" and first.source_version == source.dataset_version
    assert first.ac_online is True
    assert first.metrics["disk_free_pct"].quality == "fresh"
    assert second.metrics["disk_free_pct"].quality == "cached"
    assert second.metrics["disk_free_pct"].observation_time == first.sample_time
    labels = load_evaluation_labels(export)
    assert tuple(label.sample_id for label in labels.observations) == tuple(row.sample_id for row in source.window.records)


def test_checksum_mismatch_rejected(export):
    with (export / "telemetry.csv").open("a", encoding="utf-8") as stream:
        stream.write("tampered\n")
    with pytest.raises(ValueError, match="checksum mismatch"):
        load_telemetry(export)


@pytest.mark.parametrize("extra", ["scenario", "is_anomaly", "incident_id", "incident_time", "severity",
                                  "expected_behavior", "mystery_metric"])
def test_unknown_and_label_columns_rejected_even_with_valid_hash(export, extra):
    path = export / "telemetry.csv"
    with path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.reader(stream))
    rows[0].append(extra)
    for row in rows[1:]:
        row.append("1")
    with path.open("w", encoding="utf-8", newline="") as stream:
        csv.writer(stream).writerows(rows)
    update_checksum(export, "telemetry.csv")
    with pytest.raises(ValueError, match="unexpected telemetry CSV columns"):
        load_telemetry(export)


@pytest.mark.parametrize("problem", ["version", "count", "as_of", "non_utc", "source_version"])
def test_manifest_errors_rejected(export, problem):
    path = export / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    key, value = {
        "version": ("telemetry_schema_version", "9.0.0"),
        "count": ("record_count", manifest["record_count"] + 1),
        "as_of": ("as_of", "2025-01-01T00:00:00Z"),
        "non_utc": ("window_start", "2026-01-01T01:00:00+01:00"),
        "source_version": ("generator_version", "wrong-version"),
    }[problem]
    manifest[key] = value
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError):
        load_telemetry(export)


@pytest.mark.parametrize("overrides", [
    {"metrics": ("network_latency_ms",)}, {"metrics": ("scenario",)}, {"metrics": ()},
    {"metrics": ("cpu_usage_pct", "cpu_usage_pct")}, {"windows_seconds": (300, 120)},
    {"windows_seconds": (0,)}, {"gap_factor": 0}, {"minimum_coverage": 1.1},
    {"thresholds": {"cpu_usage_pct": {"value": 105, "direction": "high"}}},
])
def test_config_rejects_invalid_settings(overrides):
    with pytest.raises(ValidationError):
        FeatureConfig(**overrides)


def test_fingerprint_is_stable_and_sensitive_to_behavior():
    default = FeatureConfig()
    assert default.fingerprint() == FeatureConfig(metrics=default.selected_metrics()).fingerprint()
    first = Threshold(value=90, direction="high")
    second = Threshold(value=10, direction="low")
    assert FeatureConfig(thresholds={"disk_free_pct": second, "memory_usage_pct": first}).fingerprint() == default.fingerprint()
    for change in (
        {"windows_seconds": (180, 300)}, {"minimum_observations": 4}, {"gap_factor": 3},
        {"baseline_seconds": 7200}, {"profile": "synthetic_extended"}, {"thresholds": {}},
    ):
        assert FeatureConfig(**change).fingerprint() != default.fingerprint()


def test_yaml_profile_override(tmp_path):
    config = tmp_path / "features.yaml"
    config.write_text("features:\n  windows_seconds: [120, 600]\n", encoding="utf-8")
    loaded = load_config(config, "synthetic_extended")
    assert loaded.profile == "synthetic_extended" and loaded.windows_seconds == (120, 600)
