"""Versioned feature contract and offline anomaly scoring."""
from collections.abc import Iterable
from dataclasses import dataclass
from time import perf_counter

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

from ultron_health_ml.contracts.telemetry import TELEMETRY_SCHEMA_VERSION
from ultron_health_ml.features.config import FEATURE_SCHEMA_VERSION, FeatureConfig
from ultron_health_ml.features.schema import FeatureRow, feature_inventory
from ultron_health_ml.models.config import Candidate, TrainingConfig
from ultron_health_ml.models.preprocessing import Preprocessor, fit_preprocessor


@dataclass(frozen=True)
class FeatureSignature:
    telemetry_schema_version: str
    feature_schema_version: str
    feature_profile: str
    feature_config_fingerprint: str
    model_columns: tuple[str, ...]

    @classmethod
    def from_config(cls, config: FeatureConfig):
        return cls(TELEMETRY_SCHEMA_VERSION, FEATURE_SCHEMA_VERSION, config.profile,
                   config.fingerprint(), tuple(feature_inventory(config)))

    def check_row(self, row: FeatureRow):
        actual = FeatureSignature(row.telemetry_schema_version, row.feature_schema_version,
                                  row.feature_profile, row.feature_config_fingerprint, row.feature_names)
        if actual != self:
            raise ValueError("feature row is incompatible with the model signature")


@dataclass(frozen=True)
class AnomalyModel:
    signature: FeatureSignature
    preprocessor: Preprocessor
    estimator: IsolationForest
    threshold: float

    def score_frame(self, frame: pd.DataFrame, signature: FeatureSignature) -> np.ndarray:
        if signature != self.signature:
            raise ValueError("incompatible feature signature")
        return -self.estimator.score_samples(self.preprocessor.transform(frame))

    def predict_features(self, rows: Iterable[FeatureRow]) -> tuple[np.ndarray, np.ndarray]:
        records = []
        for row in rows:
            self.signature.check_row(row)
            records.append(row.model_features())
        frame = pd.DataFrame(records, columns=self.signature.model_columns, dtype=float)
        if frame.empty:
            return np.empty(0), np.empty(0, dtype=bool)
        scores = self.score_frame(frame, self.signature)
        return scores, scores >= self.threshold


def fit_model(frame: pd.DataFrame, signature: FeatureSignature, settings: TrainingConfig,
              candidate: Candidate, *, timings: dict | None = None) -> AnomalyModel:
    if tuple(frame.columns) != signature.model_columns:
        raise ValueError("training columns do not match the feature signature")
    started = perf_counter()
    fitted = fit_preprocessor(frame, settings, scale=candidate.scale)
    values = fitted.transform(frame)
    if timings is not None:
        timings["preprocessing_seconds"] = perf_counter() - started
    estimator = IsolationForest(n_estimators=candidate.n_estimators, max_samples=candidate.max_samples,
                                max_features=candidate.max_features, contamination="auto",
                                n_jobs=1, random_state=settings.seed)
    started = perf_counter()
    estimator.fit(values)
    if timings is not None:
        timings["estimator_fit_seconds"] = perf_counter() - started
    return AnomalyModel(signature, fitted, estimator, float("inf"))
