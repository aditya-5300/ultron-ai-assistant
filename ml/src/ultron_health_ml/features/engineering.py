"""One past-only transformer for offline datasets and future inference windows."""

import argparse
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import yaml

from ultron_health_ml.contracts.telemetry import DEEP_METRICS, TelemetryRecord, TelemetryWindow
from ultron_health_ml.features.config import FeatureConfig, load_config
from ultron_health_ml.features.schema import FeatureRow, NO_TREND_METRICS, WindowQuality, feature_inventory, selected_pairs
from ultron_health_ml.features.statistics import changes, correlation, describe, historical_baseline, movement
from ultron_health_ml.features.windows import History, MICROSECONDS, assess, timestamp_ticks

POWER_SENSITIVE = frozenset({"cpu_usage_pct", "battery_pct"})


class _Pipeline:
    def __init__(self, config: FeatureConfig, dataset_version: str | None):
        self.config = config
        self.dataset_version = dataset_version
        self.names = tuple(feature_inventory(config))
        self.fingerprint = config.fingerprint()
        self.histories = {name: History[float]() for name in config.selected_metrics()}
        self.pairs = {name: (pair, History[tuple[float, float]]()) for name, pair in selected_pairs(config).items()}
        self.last_observations: dict[str, tuple[int, float]] = {}
        self.deep_windows = {}
        self.lookback = max(config.windows_seconds[-1], config.baseline_seconds) * MICROSECONDS
        self.previous: TelemetryRecord | None = None
        self.previous_time: int | None = None

    def _reading(self, record: TelemetryRecord, end: int, name: str, history: History[float],
                 breaks: Counter, duplicates: list[str]) -> tuple[float | None, int | None, dict]:
        reading = record.metrics.get(name)
        observed = timestamp_ticks(reading.observation_time) if reading and reading.observation_time else None
        age = (end - observed) / MICROSECONDS if observed is not None else None
        available = reading is not None and reading.availability == "available"
        stale = available and (reading.quality == "stale" or age >= self.config.max_cached_age_seconds)
        value = reading.value if available and not stale else None
        quality = {"current": value, "available": int(available),
                   "fresh": int(available and reading.quality == "fresh"),
                   "cached": int(available and not stale and reading.quality == "cached"),
                   "stale": int(stale), "observation_age_seconds": age}
        repeated = False
        if observed is not None:
            prior = self.last_observations.get(name)
            if prior and (observed < prior[0] or (observed == prior[0] and reading.value != prior[1])):
                raise ValueError(f"inconsistent or out-of-order observation for {name} at {record.sample_id}")
            repeated = prior is not None and observed == prior[0]
            self.last_observations[name] = (observed, reading.value)
        if value is None:
            if history.times:
                breaks["unusable_metric"] += 1
            history.clear(end)
        elif repeated:
            if name in DEEP_METRICS:
                duplicates.append(name)
        elif history.floor is None or observed >= history.floor:
            cadence = self.config.deep_interval_seconds if name in DEEP_METRICS else record.nominal_interval_seconds
            if history.times and observed - history.times[-1] > self.config.gap_factor * cadence * MICROSECONDS:
                history.clear(observed)
                breaks["sensor_gap"] += 1
            history.append(observed, value)
        return value, observed, quality

    def _metric_windows(self, name: str, history: History[float], end: int, observed: int | None,
                        current: float | None, quality: dict[str, WindowQuality]) -> dict[str, float | None]:
        config = self.config
        history.prune(end - self.lookback)
        windows = [(duration, history.bounds(end, duration)) for duration in config.windows_seconds]
        comparable = current is not None and bool(history.times) and history.times[-1] == observed
        baseline = history.bounds(end, config.baseline_seconds, before=observed) if comparable else slice(0, 0)
        cache_key = None
        if name in DEEP_METRICS:
            # Reuse only when every selected measurement and the baseline reference are unchanged.
            cache_key = (len(history.times), history.times[0] if history.times else None,
                         history.times[-1] if history.times else None, observed,
                         tuple((bounds.start, bounds.stop) for _, bounds in windows), baseline.start, baseline.stop)
            cached = self.deep_windows.get(name)
            if cached is not None and cached[0] == cache_key:
                quality.update(cached[2])
                return cached[1]
        times = np.asarray(history.times, dtype=np.int64)
        values = np.asarray(history.values, dtype=float)
        gaps = np.diff(times)
        outputs, diagnostics = {}, {}
        for duration, bounds in windows:
            evidence = assess(times, gaps, bounds, duration, config.minimum_observations, config.minimum_coverage)
            diagnostics[f"{name}__window_{duration}s"] = evidence
            if evidence.valid:
                result = describe(values[bounds])
                if name not in NO_TREND_METRICS:
                    result.update(changes(times[bounds], values[bounds], config.epsilon,
                                          config.minimum_trend_seconds, mean=result["mean"]))
                outputs.update({f"{key}_{duration}s": value for key, value in result.items()})
        # A cached current value cannot become part of its own baseline on subsequent rows.
        evidence = assess(times, gaps, baseline, config.baseline_seconds,
                          config.baseline_min_observations, config.baseline_min_coverage)
        diagnostics[f"{name}__baseline_{config.baseline_seconds}s"] = evidence
        if evidence.valid:
            outputs.update(historical_baseline(values[baseline], current, config.epsilon))
        if name not in NO_TREND_METRICS:
            increases, decreases = movement(history.values[history.bounds(end, config.windows_seconds[-1])])
            outputs.update(consecutive_increases=increases, consecutive_decreases=decreases)
        if cache_key is not None:
            self.deep_windows[name] = (cache_key, outputs, diagnostics)
        quality.update(diagnostics)
        return outputs

    def _cross_metrics(self, end: int, outputs: dict, quality: dict, breaks: Counter, sampling_gap: bool,
                       context_changed: bool):
        current = lambda name: outputs.get(f"{name}__current")
        if "cpu_memory_pressure" in self.names:
            cpu, memory = current("cpu_usage_pct"), current("memory_usage_pct")
            outputs["cpu_memory_pressure"] = min(cpu, memory) if cpu is not None and memory is not None else None
        for name, (pair, history) in self.pairs.items():
            if sampling_gap or (context_changed and POWER_SENSITIVE.intersection(pair)):
                history.clear(end)
            if all(outputs[f"{metric}__fresh"] and current(metric) is not None for metric in pair):
                history.append(end, (current(pair[0]), current(pair[1])))
            else:
                if history.times:
                    breaks["unusable_pair"] += 1
                history.clear(end)
            duration = self.config.cross_window_seconds
            history.prune(end - duration * MICROSECONDS)
            bounds = history.bounds(end, duration)
            times = np.asarray(history.times, dtype=np.int64)
            evidence = assess(times, np.diff(times), bounds, duration, self.config.minimum_observations,
                              self.config.minimum_coverage)
            column = f"{name}_{duration}s"
            quality[column] = evidence
            if evidence.valid:
                left, right = np.asarray(history.values[bounds], dtype=float).T
                outputs[column] = correlation(left, right, self.config.epsilon)

    def transform(self, record: TelemetryRecord) -> FeatureRow:
        end = timestamp_ticks(record.sample_time)
        previous = self.previous
        context_changed = previous is not None and (previous.ac_online, previous.power_plan) != (record.ac_online, record.power_plan)
        sampling_gap = previous is not None and end - self.previous_time > self.config.gap_factor * min(
            previous.nominal_interval_seconds, record.nominal_interval_seconds) * MICROSECONDS
        quality, outputs = {}, {}
        breaks = Counter(sampling_gap=int(sampling_gap), context_change=int(context_changed))
        duplicates = []
        with np.errstate(over="ignore", invalid="ignore"):
            for name, history in self.histories.items():
                if sampling_gap or (context_changed and name in POWER_SENSITIVE):
                    history.clear(end)
                current, observed, fields = self._reading(record, end, name, history, breaks, duplicates)
                fields.update(self._metric_windows(name, history, end, observed, current, quality))
                threshold = self.config.thresholds.get(name)
                if threshold and current is not None:
                    fields["threshold_margin"] = threshold.value - current if threshold.direction == "high" else current - threshold.value
                outputs.update({f"{name}__{key}": value for key, value in fields.items()})
            self._cross_metrics(end, outputs, quality, breaks, sampling_gap, context_changed)
        self.previous, self.previous_time = record, end
        return FeatureRow(
            sample_id=record.sample_id, sample_time=record.sample_time, as_of=record.sample_time,
            source=record.source, source_version=record.source_version, dataset_version=self.dataset_version,
            telemetry_schema_version=record.telemetry_schema_version, feature_profile=self.config.profile,
            feature_config_fingerprint=self.fingerprint, ac_online=record.ac_online, power_plan=record.power_plan,
            context_changed=context_changed, sampling_gap=sampling_gap, feature_names=self.names, values=outputs,
            window_quality=quality, continuity_breaks=dict(breaks), cached_duplicates=tuple(duplicates),
        )


def iter_features(window: TelemetryWindow, config: FeatureConfig, *,
                  dataset_version: str | None = None) -> Iterator[FeatureRow]:
    """Validate once, then process each acquisition in chronological order without lookahead."""
    window = TelemetryWindow.model_validate(window)
    config = FeatureConfig.model_validate(config)
    if config.profile == "synthetic_extended" and window.records[0].source != "synthetic":
        raise ValueError("synthetic_extended requires synthetic telemetry")
    pipeline = _Pipeline(config, dataset_version)
    for record in window.records:
        yield pipeline.transform(record)


def main():
    from ultron_health_ml.features.export import write_features
    from ultron_health_ml.features.validation import load_telemetry

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--profile", choices=("real_ultron", "synthetic_extended"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        config = load_config(args.config, args.profile)
        source = load_telemetry(args.input)
        rows = iter_features(source.window, config, dataset_version=source.dataset_version)
        manifest = write_features(source, config, rows, args.output)
    except (ValueError, OSError, yaml.YAMLError) as error:
        parser.exit(2, f"Feature generation error: {error}\n")
    report = manifest["quality"]
    print(f"Wrote {report['feature_rows']} rows × {report['feature_count']} features in "
          f"{manifest['feature_export_seconds']:.2f}s ({config.profile}) to {args.output}")


if __name__ == "__main__":
    main()
