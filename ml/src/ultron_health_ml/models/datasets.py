"""Generate independent runs and load only the approved feature projection."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import file_digest
import json
from pathlib import Path
from random import Random
from time import perf_counter

import pandas as pd

from ultron_health_ml.data_generation.config import GeneratorConfig, ScenarioConfig
from ultron_health_ml.data_generation.generator import generate, write_dataset
from ultron_health_ml.data_generation.scenarios import PROFILES
from ultron_health_ml.features.engineering import iter_features
from ultron_health_ml.features.export import write_features
from ultron_health_ml.features.schema import METADATA_COLUMNS
from ultron_health_ml.features.validation import load_telemetry
from ultron_health_ml.models.isolation_forest import FeatureSignature
from ultron_health_ml.models.preprocessing import numeric_matrix


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


@dataclass(frozen=True)
class Run:
    split: str
    seed: int
    directory: Path
    frame: pd.DataFrame
    ids: tuple[str, ...]
    times: pd.DatetimeIndex
    telemetry_hash: str
    feature_hash: str
    feature_load_seconds: float

    @property
    def name(self):
        return f"{self.split}-{self.seed}"


def load_run(directory, split, seed, signature):
    started = perf_counter()
    manifest = json.loads((directory / "features/feature_manifest.json").read_text(encoding="utf-8"))
    for field in ("telemetry_schema_version", "feature_schema_version", "feature_profile", "feature_config_fingerprint"):
        if manifest.get(field) != getattr(signature, field):
            raise ValueError(f"incompatible export: {field}")
    columns = (*METADATA_COLUMNS, *signature.model_columns)
    if (tuple(manifest.get("model_columns", [])) != signature.model_columns
            or tuple(manifest.get("metadata_columns", [])) != METADATA_COLUMNS):
        raise ValueError("unapproved feature export columns")
    path = directory / "features/features.csv"
    checksum = sha256(path)
    telemetry_hash = sha256(directory / "telemetry.csv")
    if checksum != manifest["features_csv_sha256"] or telemetry_hash != manifest["input_telemetry_sha256"]:
        raise ValueError("feature or input telemetry checksum mismatch")
    table = pd.read_csv(path, keep_default_na=False, na_values={name: [""] for name in signature.model_columns},
                        dtype={**dict.fromkeys(signature.model_columns, float), "sample_id": str})
    if (tuple(table.columns) != columns or not isinstance(table.index, pd.RangeIndex)
            or not len(table) or len(table) != manifest["quality"]["feature_rows"]):
        raise ValueError("invalid feature export header or row count")
    times = pd.DatetimeIndex(pd.to_datetime(table["sample_time"], format="ISO8601"))
    ids = tuple(table["sample_id"])
    if (str(times.tz) != "UTC" or times.hasnans or not times.is_monotonic_increasing or not times.is_unique
            or not len(set(ids)) == len(ids) or "" in ids or not table["sample_time"].equals(table["as_of"])):
        raise ValueError("invalid sample identities, timestamps, or as-of cutoff")
    for field in ("telemetry_schema_version", "feature_schema_version", "feature_profile", "feature_config_fingerprint"):
        if not table[field].eq(getattr(signature, field)).all():
            raise ValueError(f"incompatible feature row: {field}")
    frame = table.loc[:, list(signature.model_columns)]
    numeric_matrix(frame, signature.model_columns)
    return Run(split, seed, directory, frame, ids, times, telemetry_hash, checksum, perf_counter() - started)


def generate_run(root, split, seed, days, config):
    rng = Random(seed)
    kinds = [name for name, profile in PROFILES.items() if split != "train" or not profile[2]]
    rng.shuffle(kinds)
    scenarios = tuple(
        ScenarioConfig(scenario_type=name, start_fraction=0.04 + index * 0.077 + rng.uniform(0, 0.008),
                       duration_fraction=min(0.045, 90 / (days * 86400)) if name == "CPU_SPIKE"
                       else (0.12 if split == "train" else 0.045), intensity=rng.uniform(0.85, 1.25))
        for index, name in enumerate(kinds)
    )
    if split == "train":
        scenarios = tuple(item.model_copy(update={"start_fraction": 0.18 + index * 0.27})
                          for index, item in enumerate(scenarios))
    directory = root / f"{split}-{seed}"
    settings = GeneratorConfig(days=days, random_seed=seed, scenarios=scenarios, jitter_seconds=2,
                               skip_probability=0.005,
                               start_time=datetime(2026, 1, 1, tzinfo=timezone.utc)
                               + timedelta(hours=rng.randrange(24)))
    write_dataset(generate(settings), directory)
    source = load_telemetry(directory)
    write_features(source, config, iter_features(source.window, config, dataset_version=source.dataset_version),
                   directory / "features")
    return load_run(directory, split, seed, FeatureSignature.from_config(config))
