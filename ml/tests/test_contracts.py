from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from ultron_health_ml.contracts.telemetry import (
    DECLARED_MODEL_INPUT_FIELDS, REAL_ULTRON_METRICS, SYNTHETIC_APPLICATION_METRICS,
    MetricReading, TelemetryRecord, TelemetryWindow,
)

START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def record(offset=0, **overrides):
    timestamp = START + timedelta(seconds=offset)
    fields = dict(source="synthetic", source_version="0.1.0", sample_id=str(offset),
                  sample_time=timestamp, nominal_interval_seconds=30,
                  elapsed_seconds=offset or None,
                  metrics={"cpu_usage_pct": MetricReading(value=40, observation_time=timestamp,
                           availability="available", quality="fresh")})
    return TelemetryRecord(**(fields | overrides))


def window(records, **overrides):
    return TelemetryWindow(**(dict(window_start=START, window_end=START + timedelta(seconds=120),
                                  as_of=START + timedelta(seconds=120), records=records) | overrides))


def test_missing_is_not_zero():
    reading = MetricReading(value=None, observation_time=None, availability="unavailable", quality="missing")
    assert reading.value is None and reading.usable_value() is None
    with pytest.raises(ValidationError):
        MetricReading(value=0, observation_time=None, availability="unavailable", quality="missing")


@pytest.mark.parametrize("value", [True, "45", float("nan"), float("inf"), -1, 101])
def test_invalid_metric_values_rejected(value):
    with pytest.raises(ValidationError):
        record(metrics={"cpu_usage_pct": dict(value=value, observation_time=START,
                                             availability="available", quality="fresh")})


def test_stale_retains_evidence_but_not_usable_value():
    reading = MetricReading(value=60, observation_time=START, availability="available", quality="stale")
    assert reading.value == 60 and reading.usable_value() is None
    assert record(60, metrics={"disk_free_pct": reading})


def test_unknown_metrics_and_real_application_metrics_rejected():
    reading = MetricReading(value=5, observation_time=START, availability="available", quality="fresh")
    for name, source in [("is_anomaly", "synthetic"), ("network_latency_ms", "ultron")]:
        with pytest.raises(ValidationError):
            record(source=source, metrics={name: reading})


def test_labels_not_input_fields_or_contract_fields():
    labels = {"scenario", "is_anomaly", "incident_time", "severity", "scenario_id", "incident_ids"}
    assert not labels.intersection(DECLARED_MODEL_INPUT_FIELDS)
    assert set(DECLARED_MODEL_INPUT_FIELDS) == set(REAL_ULTRON_METRICS) | set(SYNTHETIC_APPLICATION_METRICS)
    with pytest.raises(ValidationError):
        record(is_anomaly=True)


@pytest.mark.parametrize("records", [[record(), record()], [record(60), record()]])
def test_duplicate_or_out_of_order_times_rejected(records):
    with pytest.raises(ValidationError, match="duplicate or out-of-order"):
        window(records)


def test_real_elapsed_gap_preserved():
    result = window([record(), record(91)])
    assert result.records[-1].elapsed_seconds == 91
    with pytest.raises(ValidationError, match="elapsed_seconds"):
        window([record(), record(91, elapsed_seconds=30)])


def test_future_and_outside_window_rejected():
    with pytest.raises(ValidationError):
        window([record()], as_of=START - timedelta(seconds=1))
    with pytest.raises(ValidationError, match="outside"):
        window([record(150)])
    with pytest.raises(ValidationError, match="outside"):
        window([record(-30, elapsed_seconds=None)])
    with pytest.raises(ValidationError, match="later than sample_time"):
        record(metrics={"cpu_usage_pct": dict(value=40, observation_time=START + timedelta(seconds=1),
                                             availability="available", quality="fresh")})


@pytest.mark.parametrize("timestamp", [START.replace(tzinfo=None), START.astimezone(timezone(timedelta(hours=5)))])
def test_non_utc_or_naive_timestamps_rejected(timestamp):
    with pytest.raises(ValidationError, match="UTC"):
        record(sample_time=timestamp)


def test_cached_time_and_freshness_consistency():
    with pytest.raises(ValidationError, match="older"):
        record(metrics={"disk_free_pct": dict(value=60, observation_time=START,
                                             availability="available", quality="cached")})
    with pytest.raises(ValidationError, match="fresh readings"):
        record(60, metrics={"cpu_usage_pct": dict(value=40, observation_time=START,
                                                availability="available", quality="fresh")})


def test_versions_and_extra_fields_rejected():
    with pytest.raises(ValidationError):
        record(telemetry_schema_version="2.0.0")
    with pytest.raises(ValidationError):
        window([record()], scenario="NORMAL")


def test_source_and_id_consistency():
    with pytest.raises(ValidationError, match="duplicate sample_id"):
        window([record(), record(30, sample_id="0")])
    with pytest.raises(ValidationError, match="one declared source"):
        window([record(), record(30, source_version="other")])


def test_tampered_instance_is_revalidated():
    invalid = record().model_copy(update={"sample_time": START + timedelta(days=1)})
    with pytest.raises(ValidationError):
        window([invalid])


def test_json_roundtrip():
    original = window([record(), record(30)])
    assert TelemetryWindow.model_validate_json(original.model_dump_json()) == original
