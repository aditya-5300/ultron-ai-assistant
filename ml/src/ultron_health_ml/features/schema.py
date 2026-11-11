"""Model columns are defined here, separately from identifiers and diagnostics."""

from dataclasses import dataclass
from datetime import datetime
import math
from typing import Mapping

from ultron_health_ml.contracts.telemetry import METRIC_SPECS
from ultron_health_ml.features.config import FEATURE_SCHEMA_VERSION, FeatureConfig

METADATA_COLUMNS = (
    "sample_id", "sample_time", "as_of", "source", "source_version", "dataset_version",
    "telemetry_schema_version", "feature_schema_version", "feature_profile", "feature_config_fingerprint",
    "ac_online", "power_plan", "context_changed", "sampling_gap",
)
WINDOW_STATISTICS = ("mean", "median", "std", "min", "max")
CHANGE_STATISTICS = ("delta", "abs_change", "pct_change", "rate_per_hour", "slope_per_hour", "trend_r2")
BASELINE_STATISTICS = ("baseline_median", "baseline_mad", "baseline_deviation", "robust_z")
NO_TREND_METRICS = frozenset({"battery_capacity_pct", "selected_process_nonresponding_count", "relevant_event_count"})
SYNTHETIC_PAIRS = {
    "cpu_response_correlation": ("cpu_usage_pct", "response_time_ms"),
    "latency_response_correlation": ("network_latency_ms", "response_time_ms"),
    "throughput_error_correlation": ("throughput_rps", "error_rate"),
}


def selected_pairs(config: FeatureConfig) -> dict[str, tuple[str, str]]:
    metrics = set(config.selected_metrics())
    return {name: pair for name, pair in SYNTHETIC_PAIRS.items()
            if config.profile == "synthetic_extended" and set(pair) <= metrics}


def feature_inventory(config: FeatureConfig) -> dict[str, str]:
    """Return the ordered feature allowlist and units for the resolved configuration."""
    inventory = {}
    for metric in config.selected_metrics():
        unit = METRIC_SPECS[metric][2]
        difference_unit = "percentage points" if unit == "%" else unit
        columns = {
            "current": unit, "available": "binary", "fresh": "binary",
            "cached": "binary", "stale": "binary", "observation_age_seconds": "seconds",
        }
        columns["baseline_median"] = unit
        columns.update({name: difference_unit for name in BASELINE_STATISTICS[1:-1]})
        columns["robust_z"] = "unitless"
        if metric in config.thresholds:
            columns["threshold_margin"] = difference_unit
        if metric not in NO_TREND_METRICS:
            columns.update(consecutive_increases="count", consecutive_decreases="count")
        for duration in config.windows_seconds:
            columns.update({f"{name}_{duration}s": unit for name in WINDOW_STATISTICS})
            if metric not in NO_TREND_METRICS:
                change_units = (difference_unit, difference_unit, "%", f"{difference_unit}/hour",
                                f"{difference_unit}/hour", "unitless")
                columns.update({f"{name}_{duration}s": value for name, value in zip(CHANGE_STATISTICS, change_units)})
        inventory.update({f"{metric}__{name}": value for name, value in columns.items()})
    if {"cpu_usage_pct", "memory_usage_pct"} <= set(config.selected_metrics()):
        inventory["cpu_memory_pressure"] = "%"
    inventory.update({f"{name}_{config.cross_window_seconds}s": "Pearson r" for name in selected_pairs(config)})
    return inventory


def finite_or_null(value: float | None) -> float | None:
    return float(value) if value is not None and math.isfinite(value) else None


@dataclass(frozen=True)
class WindowQuality:
    valid: bool
    observation_count: int
    coverage_ratio: float
    largest_gap_seconds: float | None


@dataclass(frozen=True)
class FeatureRow:
    sample_id: str
    sample_time: datetime
    as_of: datetime
    source: str
    source_version: str
    dataset_version: str | None
    telemetry_schema_version: str
    feature_profile: str
    feature_config_fingerprint: str
    ac_online: bool | None
    power_plan: str
    context_changed: bool
    sampling_gap: bool
    feature_names: tuple[str, ...]
    values: Mapping[str, float | None]
    window_quality: Mapping[str, WindowQuality]
    continuity_breaks: Mapping[str, int]
    cached_duplicates: tuple[str, ...]
    feature_schema_version: str = FEATURE_SCHEMA_VERSION

    def model_features(self) -> dict[str, float | None]:
        return {name: finite_or_null(self.values.get(name)) for name in self.feature_names}

    def metadata(self) -> dict[str, object]:
        return {name: getattr(self, name) for name in METADATA_COLUMNS}
