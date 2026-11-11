from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from feature_helpers import telemetry
from ultron_health_ml.features.config import FeatureConfig
from ultron_health_ml.features.engineering import iter_features
from ultron_health_ml.models.config import Candidate, TrainingConfig
from ultron_health_ml.models.isolation_forest import FeatureSignature, fit_model


def test_scores_are_deterministic_and_higher_for_isolated_points():
    signature = FeatureSignature("1.0.0", "1.0.0", "real_ultron", "fixture", ("cpu",))
    train = pd.DataFrame(dict(cpu=np.linspace(0, 1, 128)))
    test = pd.DataFrame(dict(cpu=[0.5, 10.]))
    settings = TrainingConfig()
    candidate = Candidate(n_estimators=40)
    model = fit_model(train, signature, settings, candidate)
    scores = model.score_frame(test, signature)
    assert scores[1] > scores[0]
    np.testing.assert_array_equal(scores, fit_model(train, signature, settings, candidate).score_frame(test, signature))
    with pytest.raises(ValueError, match="signature"):
        model.score_frame(test, replace(signature, feature_profile="synthetic_extended"))


def test_inference_matches_feature_projection_and_checks_contract():
    config = FeatureConfig()
    signature = FeatureSignature.from_config(config)
    rows = list(iter_features(telemetry([(index * 30, {"cpu_usage_pct": float(index % 23)})
                                         for index in range(50)]), config))
    frame = pd.DataFrame([row.model_features() for row in rows], dtype=float)
    model = replace(fit_model(frame.iloc[:40], signature, TrainingConfig(),
                              Candidate(n_estimators=5)), threshold=0.55)
    scores, decisions = model.predict_features(rows[40:])
    single, _ = model.predict_features(rows[40:41])
    np.testing.assert_allclose(single, scores[:1], atol=1e-12, rtol=0)
    np.testing.assert_array_equal(decisions, scores >= 0.55)
    for changed in (replace(rows[-1], feature_schema_version="2"), replace(rows[-1], telemetry_schema_version="2"),
                    replace(rows[-1], feature_profile="synthetic_extended"),
                    replace(rows[-1], feature_config_fingerprint="wrong"),
                    replace(rows[-1], feature_names=rows[-1].feature_names[::-1])):
        with pytest.raises(ValueError, match="signature"):
            model.predict_features([changed])
