from datetime import datetime, timedelta, timezone

from ultron_health_ml.contracts.telemetry import MetricReading, TelemetryRecord, TelemetryWindow

START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def at(seconds):
    return START + timedelta(seconds=seconds)


def telemetry(samples, *, interval=30, source="synthetic", contexts=None):
    records = []
    context = (True, "balanced")
    for index, (offset, values) in enumerate(samples):
        timestamp = at(offset)
        context = (contexts or {}).get(offset, context)
        readings = {}
        for name, value in values.items():
            if isinstance(value, tuple):
                value, observed, quality = value
                readings[name] = MetricReading(value=value, observation_time=at(observed),
                                               availability="available", quality=quality)
            elif value is None:
                readings[name] = MetricReading(value=None, observation_time=None,
                                               availability="unavailable", quality="missing")
            else:
                readings[name] = MetricReading(value=value, observation_time=timestamp,
                                               availability="available", quality="fresh")
        records.append(TelemetryRecord(
            source=source, source_version="fixture-1", sample_id=f"fixture-{index}",
            sample_time=timestamp, nominal_interval_seconds=interval,
            elapsed_seconds=(timestamp - records[-1].sample_time).total_seconds() if records else None,
            ac_online=context[0], power_plan=context[1], metrics=readings,
        ))
    return TelemetryWindow(window_start=records[0].sample_time, window_end=records[-1].sample_time,
                           as_of=records[-1].sample_time, records=tuple(records))
