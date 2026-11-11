"""Stream approved feature columns and summarize their quality without fitting anything."""

from collections import Counter
from collections.abc import Iterable
import csv
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter, process_time

from ultron_health_ml.features.config import FEATURE_SCHEMA_VERSION, FeatureConfig
from ultron_health_ml.features.schema import METADATA_COLUMNS, FeatureRow, feature_inventory
from ultron_health_ml.features.validation import LoadedTelemetry


class _QualitySummary:
    def __init__(self, columns: tuple[str, ...]):
        self.columns = columns
        self.rows = 0
        self.valid_counts = [0] * len(columns)
        self.minimum = [float("inf")] * len(columns)
        self.maximum = [-float("inf")] * len(columns)
        self.invalid_windows = Counter()
        self.continuity_breaks = Counter()
        self.cached_duplicates = Counter()

    def update(self, row: FeatureRow, values: list[float | None]):
        self.rows += 1
        for index, value in enumerate(values):
            if value is not None:
                self.valid_counts[index] += 1
                if value < self.minimum[index]:
                    self.minimum[index] = value
                if value > self.maximum[index]:
                    self.maximum[index] = value
        self.invalid_windows.update(name for name, quality in row.window_quality.items() if not quality.valid)
        self.continuity_breaks.update(row.continuity_breaks)
        self.cached_duplicates.update(row.cached_duplicates)

    def report(self, raw_observations: int, tolerance: float) -> dict:
        fields = {}
        for name, count, lower, upper in zip(self.columns, self.valid_counts, self.minimum, self.maximum):
            fields[name] = {
                "observed_count": count,
                "missing_pct": 100 * (self.rows - count) / self.rows,
                "min": lower if count else None,
                "max": upper if count else None,
            }
        return {
            "raw_observations": raw_observations,
            "feature_rows": self.rows,
            "feature_count": len(self.columns),
            "per_feature": fields,
            "all_missing_features": [name for name, summary in fields.items() if not summary["observed_count"]],
            "constant_features": [name for name, summary in fields.items()
                                  if summary["observed_count"] >= 2 and summary["min"] == summary["max"]],
            "near_constant_features": [name for name, summary in fields.items()
                                       if summary["observed_count"] >= 2 and 0 < summary["max"] - summary["min"] <= tolerance],
            "invalid_windows": sum(self.invalid_windows.values()),
            "invalid_windows_by_group": dict(self.invalid_windows),
            "continuity_breaks": dict(self.continuity_breaks),
            "cached_deep_duplicates_ignored": sum(self.cached_duplicates.values()),
            "cached_deep_duplicates_by_metric": dict(self.cached_duplicates),
        }


def write_features(source: LoadedTelemetry, config: FeatureConfig, rows: Iterable[FeatureRow], output: Path) -> dict:
    started = perf_counter()
    started_cpu = process_time()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError("output directory must be empty; existing files will not be overwritten")
    inventory = feature_inventory(config)
    columns = tuple(inventory)
    fingerprint = config.fingerprint()
    summary = _QualitySummary(columns)
    raw = len(source.window.records)
    with TemporaryDirectory(dir=output, prefix=".feature-export-") as staging:
        temporary = Path(staging)
        csv_path = temporary / "features.csv"
        with csv_path.open("x", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow((*METADATA_COLUMNS, *columns))
            for row in rows:
                if (row.feature_names != columns or row.feature_config_fingerprint != fingerprint
                        or row.feature_schema_version != FEATURE_SCHEMA_VERSION):
                    raise ValueError("feature row does not match the export schema")
                if summary.rows >= raw or row.sample_id != source.window.records[summary.rows].sample_id:
                    raise ValueError("feature rows do not match the input order")
                values = list(row.model_features().values())
                writer.writerow((*row.metadata().values(), *values))
                summary.update(row, values)
        if summary.rows != raw:
            raise ValueError("feature export did not produce one row per raw observation")
        with csv_path.open("rb") as stream:
            output_checksum = hashlib.file_digest(stream, "sha256").hexdigest()
        manifest = {
            "telemetry_schema_version": source.window.telemetry_schema_version,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "dataset_version": source.dataset_version,
            "source": source.window.records[0].source,
            "source_version": source.window.records[0].source_version,
            "feature_profile": config.profile,
            "feature_config_fingerprint": fingerprint,
            "feature_config": config.model_dump(mode="json"),
            "metadata_columns": list(METADATA_COLUMNS),
            "model_columns": list(columns),
            "feature_units": inventory,
            "input_telemetry_sha256": source.telemetry_sha256,
            "features_csv_sha256": output_checksum,
            "quality": summary.report(raw, config.constant_tolerance),
            "feature_export_seconds": perf_counter() - started,
            "feature_export_cpu_seconds": process_time() - started_cpu,
            "warning": "Feature engineering does not itself predict failures. Quality counts are not model performance.",
        }
        (temporary / "feature_manifest.json").write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        csv_path.rename(output / "features.csv")
        (temporary / "feature_manifest.json").rename(output / "feature_manifest.json")
    return manifest
