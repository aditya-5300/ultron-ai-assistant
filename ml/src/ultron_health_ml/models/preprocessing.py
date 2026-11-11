"""Fit a deterministic feature mask and imputer on training rows only."""
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ultron_health_ml.models.config import TrainingConfig


def numeric_matrix(frame: pd.DataFrame, columns: tuple[str, ...]) -> np.ndarray:
    if tuple(frame.columns) != columns or len(set(columns)) != len(columns):
        raise ValueError("model columns missing, unexpected, duplicated, or out of order")
    if any(not pd.api.types.is_numeric_dtype(dtype) or pd.api.types.is_complex_dtype(dtype)
           for dtype in frame.dtypes):
        raise ValueError("model features must be real numeric or null")
    values = frame.to_numpy(dtype=float, na_value=np.nan)
    if np.isinf(values).any():
        raise ValueError("model features cannot be infinite")
    return values


@dataclass(frozen=True)
class Preprocessor:
    columns: tuple[str, ...]
    selected: tuple[str, ...]
    dropped: dict[str, str]
    medians: tuple[float, ...]
    means: tuple[float, ...] | None
    scales: tuple[float, ...] | None

    def transform(self, frame: pd.DataFrame) -> np.ndarray:
        values = numeric_matrix(frame, self.columns)
        positions = {name: index for index, name in enumerate(self.columns)}
        indices = [positions[name] for name in self.selected]
        selected = values[:, indices]
        filled = np.where(np.isnan(selected), self.medians, selected)
        return filled if self.means is None else (filled - self.means) / self.scales


def fit_preprocessor(frame: pd.DataFrame, settings: TrainingConfig, *, scale: bool = False) -> Preprocessor:
    columns = tuple(frame.columns)
    values = numeric_matrix(frame, columns)
    if not len(values) or set(settings.excluded_features) - set(columns):
        raise ValueError("empty training matrix or unknown excluded feature")
    selected, dropped, medians = [], {}, []
    for index, name in enumerate(columns):
        observed = values[np.isfinite(values[:, index]), index]
        if name in settings.excluded_features:
            reason = "unsupported"
        elif not len(observed):
            reason = "all_missing"
        elif 1 - len(observed) / len(values) > settings.max_missing_fraction:
            reason = "high_missing"
        elif len(observed) < settings.min_observations:
            reason = "insufficient_observations"
        elif np.ptp(observed) == 0:
            reason = "constant"
        elif np.ptp(observed) <= settings.near_constant_tolerance:
            reason = "near_constant"
        else:
            selected.append(name)
            medians.append(float(np.median(observed)))
            continue
        dropped[name] = reason
    if not selected:
        raise ValueError("no usable training features")
    fitted = Preprocessor(columns, tuple(selected), dropped, tuple(medians), None, None)
    if not scale:
        return fitted
    imputed = fitted.transform(frame)
    return Preprocessor(columns, fitted.selected, dropped, fitted.medians,
                        tuple(imputed.mean(axis=0)), tuple(imputed.std(axis=0)))
