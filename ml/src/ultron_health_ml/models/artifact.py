"""Persist and verify an offline artifact. Never unpickle an untrusted model."""
from dataclasses import asdict
from importlib.metadata import version
import json
from pathlib import Path
import platform

import joblib
import numpy as np
from sklearn.ensemble import IsolationForest

from ultron_health_ml.models.config import MODEL_NAME, MODEL_VERSION, SCORE_ORIENTATION
from ultron_health_ml.models.datasets import sha256
from ultron_health_ml.models.isolation_forest import AnomalyModel, FeatureSignature


ARTIFACT_FILES = ("model.joblib", "metadata.json", "selected_features.json", "dropped_features.json",
                  "evaluation.json", "config_snapshot.yaml")


def library_versions():
    return {"python": platform.python_version(), **{
        name: version(name) for name in ("numpy", "pandas", "scikit-learn", "joblib", "pydantic", "PyYAML")
    }}


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def seal(path):
    write_json(path / "checksums.json", {name: sha256(path / name) for name in ARTIFACT_FILES})


def save_model_artifact(path: Path, model: AnomalyModel, metadata, evaluation, config_yaml):
    path.mkdir(parents=True, exist_ok=True)
    if any(path.iterdir()):
        raise ValueError("artifact directory must be empty")
    joblib.dump(model, path / "model.joblib", compress=3)
    write_json(path / "metadata.json", {**metadata, "model_name": MODEL_NAME, "model_version": MODEL_VERSION,
               "algorithm": "sklearn.ensemble.IsolationForest", "signature": asdict(model.signature),
               "score_orientation": SCORE_ORIENTATION, "threshold": model.threshold, "libraries": library_versions()})
    write_json(path / "selected_features.json", list(model.preprocessor.selected))
    write_json(path / "dropped_features.json", model.preprocessor.dropped)
    write_json(path / "evaluation.json", evaluation)
    (path / "config_snapshot.yaml").write_text(config_yaml, encoding="utf-8")
    seal(path)


def load_model_artifact(path: Path, expected_signature: FeatureSignature, *, trusted: bool = False) -> AnomalyModel:
    if not trusted:
        raise ValueError("joblib artifacts must come from a trusted local source")
    try:
        expected = json.loads((path / "checksums.json").read_text(encoding="utf-8"))
        if set(expected) != set(ARTIFACT_FILES):
            raise ValueError("invalid artifact checksum inventory")
        for name in ARTIFACT_FILES:
            if sha256(path / name) != expected[name]:
                raise ValueError(f"artifact checksum mismatch: {name}")
        metadata = json.loads((path / "metadata.json").read_text(encoding="utf-8"))
        if (metadata["model_name"] != MODEL_NAME or metadata["model_version"] != MODEL_VERSION
                or metadata["score_orientation"] != SCORE_ORIENTATION
                or metadata["algorithm"] != "sklearn.ensemble.IsolationForest"):
            raise ValueError("unsupported model version or score contract")
        if metadata["signature"] != json.loads(json.dumps(asdict(expected_signature))):
            raise ValueError("artifact feature signature mismatch")
        for name in ("scikit-learn", "numpy", "joblib"):
            if metadata["libraries"][name] != version(name):
                raise ValueError(f"incompatible artifact library: {name}")
        selected = tuple(json.loads((path / "selected_features.json").read_text(encoding="utf-8")))
        dropped = json.loads((path / "dropped_features.json").read_text(encoding="utf-8"))
    except (OSError, KeyError, TypeError) as error:
        raise ValueError("incomplete or invalid artifact") from error
    try:
        model = joblib.load(path / "model.joblib")
        if not isinstance(model, AnomalyModel) or model.signature != expected_signature:
            raise ValueError("model payload signature mismatch")
        fitted = model.preprocessor
        columns = expected_signature.model_columns
        if (not selected or fitted.columns != columns or fitted.selected != selected
                or tuple(name for name in columns if name not in dropped) != selected
                or fitted.dropped != dropped or set(dropped) != set(columns) - set(selected)
                or len(fitted.medians) != len(selected) or not np.isfinite(fitted.medians).all()
                or (fitted.means is None) != (fitted.scales is None)
                or (fitted.means is not None and
                    (len(fitted.means) != len(selected) or len(fitted.scales) != len(selected)
                     or not np.isfinite(fitted.means).all() or not np.isfinite(fitted.scales).all()
                     or min(fitted.scales) <= 0))
                or not isinstance(model.estimator, IsolationForest)
                or model.estimator.n_features_in_ != len(selected) or not model.estimator.estimators_
                or not np.isfinite(model.threshold) or model.threshold != metadata["threshold"]):
            raise ValueError("artifact selected features, fitted preprocessing, or threshold mismatch")
    except Exception as error:
        raise ValueError(f"invalid model payload: {error}") from error
    return model
