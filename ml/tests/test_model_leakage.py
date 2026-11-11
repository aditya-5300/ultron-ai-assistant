from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pandas as pd

from feature_helpers import telemetry
from ultron_health_ml.data_generation.scenarios import EvaluationTruth, ObservationTruth
from ultron_health_ml.features.config import FeatureConfig
from ultron_health_ml.features.engineering import iter_features
from ultron_health_ml.models.config import Candidate, TrainingConfig
from ultron_health_ml.models.evaluation import align_truth, observability
from ultron_health_ml.models.isolation_forest import FeatureSignature, fit_model


def test_future_telemetry_and_relabeling_do_not_change_past_scores():
    config = FeatureConfig()
    signature = FeatureSignature.from_config(config)
    base = [(index * 30, dict(cpu_usage_pct=float(15 + index % 17))) for index in range(75)]
    altered = base[:65] + [(stamp, dict(cpu_usage_pct=99.)) for stamp, _ in base[65:]]
    original = list(iter_features(telemetry(base), config))
    changed = list(iter_features(telemetry(altered), config))
    columns = signature.model_columns
    assert not {"scenario", "sample_id", "incident_time", "is_anomaly"} & set(columns)
    frame = pd.DataFrame([row.model_features() for row in original[:40]], columns=columns, dtype=float)
    model = replace(fit_model(frame, signature, TrainingConfig(), Candidate(n_estimators=5)), threshold=0.55)
    first, _ = model.predict_features(original[40:65])
    second, _ = model.predict_features(changed[40:65])
    np.testing.assert_array_equal(first, second)
    ids = tuple(row.sample_id for row in original[40:65])
    run = SimpleNamespace(ids=ids)
    labels = EvaluationTruth(scenarios=(), observations=tuple(ObservationTruth(sample_id=key) for key in ids))
    relabeled = labels.model_copy(update={"observations": tuple(
        ObservationTruth(sample_id=key, scenario=("CPU_SPIKE",), is_anomaly=True) for key in ids
    )})
    visibility = observability(config.selected_metrics())
    assert not align_truth(run, labels, visibility).actual.any()
    assert align_truth(run, relabeled, visibility).actual.all()
    np.testing.assert_array_equal(first, model.predict_features(original[40:65])[0])
