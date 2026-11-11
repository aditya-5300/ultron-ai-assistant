"""Load Phase 2 exports without inspecting evaluation labels."""

import csv
from dataclasses import dataclass
from datetime import datetime
import hashlib
import io
import json
from pathlib import Path
import re
from typing import TYPE_CHECKING

from ultron_health_ml.contracts.telemetry import (
    METRIC_SPECS, TELEMETRY_SCHEMA_VERSION, TelemetryRecord, TelemetryWindow, utc,
)

if TYPE_CHECKING:
    from ultron_health_ml.data_generation.scenarios import EvaluationTruth

RECORD_COLUMNS = frozenset(TelemetryRecord.model_fields) - {"metrics"}
READING_SUFFIXES = ("", "__observation_time", "__availability", "__quality")


@dataclass(frozen=True)
class LoadedTelemetry:
    window: TelemetryWindow
    dataset_version: str
    telemetry_sha256: str


def _manifest(directory: Path) -> dict:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("telemetry_schema_version") != TELEMETRY_SCHEMA_VERSION:
        raise ValueError("unsupported telemetry manifest version")
    if type(manifest.get("record_count")) is not int or not 1 <= manifest["record_count"] <= 50_000:
        raise ValueError("manifest record_count must be between 1 and 50,000")
    version = manifest.get("generator_version")
    if not isinstance(version, str) or not version or len(version) > 100:
        raise ValueError("missing or invalid generator version")
    return manifest


def _verified_bytes(directory: Path, name: str, manifest: dict) -> tuple[bytes, str]:
    checksums = manifest.get("sha256")
    expected = checksums.get(name) if isinstance(checksums, dict) else None
    if not isinstance(expected, str) or not re.fullmatch("[a-f0-9]{64}", expected):
        raise ValueError(f"missing or invalid checksum for {name}")
    data = (directory / name).read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise ValueError(f"checksum mismatch for {name}")
    return data, actual


def _timestamp(value: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("expected an ISO UTC timestamp")
    return utc(datetime.fromisoformat(value))


def _metric_columns(header: list[str] | None) -> tuple[str, ...]:
    names = set(header or ())
    if not header or len(names) != len(header) or not RECORD_COLUMNS <= names:
        raise ValueError("missing or duplicate telemetry CSV columns")
    present = tuple(name for name in METRIC_SPECS if any(name + suffix in names for suffix in READING_SUFFIXES))
    expected = RECORD_COLUMNS | {name + suffix for name in present for suffix in READING_SUFFIXES}
    if not present or names != expected:
        raise ValueError("unexpected telemetry CSV columns or incomplete metric quality columns")
    return present


def _record(raw: dict[str, str], metrics: tuple[str, ...]) -> TelemetryRecord:
    if None in raw or any(value is None for value in raw.values()):
        raise ValueError("CSV row length does not match its header")
    readings = {}
    for name in metrics:
        value, observed, available, quality = (raw[name + suffix] for suffix in READING_SUFFIXES)
        if not (value or observed or available or quality):
            continue
        readings[name] = dict(value=float(value) if value else None,
                              observation_time=_timestamp(observed) if observed else None,
                              availability=available, quality=quality)
    ac_online = raw["ac_online"].lower()
    if ac_online not in {"", "true", "false"}:
        raise ValueError("invalid AC state")
    return TelemetryRecord(
        **{name: raw[name] for name in ("telemetry_schema_version", "source", "source_version", "sample_id", "power_plan")},
        sample_time=_timestamp(raw["sample_time"]), nominal_interval_seconds=int(raw["nominal_interval_seconds"]),
        elapsed_seconds=float(raw["elapsed_seconds"]) if raw["elapsed_seconds"] else None,
        collection_duration_seconds=float(raw["collection_duration_seconds"]),
        ac_online=ac_online == "true" if ac_online else None, metrics=readings,
    )


def load_telemetry(directory: Path) -> LoadedTelemetry:
    manifest = _manifest(directory)
    data, checksum = _verified_bytes(directory, "telemetry.csv", manifest)
    reader = csv.DictReader(io.StringIO(data.decode("utf-8"), newline=""))
    metrics = _metric_columns(reader.fieldnames)
    records = []
    for raw in reader:
        if len(records) >= manifest["record_count"]:
            raise ValueError("CSV has more records than declared")
        try:
            record = _record(raw, metrics)
        except ValueError as error:
            raise ValueError(f"invalid telemetry at CSV line {reader.line_num}: {error}") from error
        if record.source != "synthetic" or record.source_version != manifest["generator_version"]:
            raise ValueError("Phase 2 export does not match its declared synthetic generator version")
        records.append(record)
    if len(records) != manifest["record_count"]:
        raise ValueError("CSV record count does not match manifest")
    window = TelemetryWindow(
        window_start=_timestamp(manifest.get("window_start")), window_end=_timestamp(manifest.get("window_end")),
        as_of=_timestamp(manifest.get("as_of")), records=tuple(records),
    )
    return LoadedTelemetry(window, manifest["generator_version"], checksum)


def load_evaluation_labels(directory: Path) -> "EvaluationTruth":
    """Explicit evaluation-only access; never called by feature generation."""
    from ultron_health_ml.data_generation.scenarios import EvaluationTruth

    data, _ = _verified_bytes(directory, "scenarios.json", _manifest(directory))
    labels = EvaluationTruth.model_validate_json(data)
    if labels.evaluation_schema_version != "1.0.0" or not labels.synthetic_only:
        raise ValueError("unsupported synthetic evaluation label schema")
    ids = [label.sample_id for label in labels.observations]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate evaluation sample IDs")
    return labels
