import numpy as np
import pandas as pd
import pytest

from ultron_health_ml.models.config import TrainingConfig
from ultron_health_ml.models.preprocessing import fit_preprocessor


def test_train_only_audit_and_median():
    train = pd.DataFrame(dict(keep=[2., 8., np.nan, 14.], missing=[np.nan]*4, constant=[7.]*4,
                              sparse=[3., np.nan, np.nan, np.nan],
                              near=[1., 1., 1., 1.000000001], unsupported=[1., 2., 3., 4.]))
    settings = TrainingConfig(min_observations=2, excluded_features=("unsupported",))
    fitted = fit_preprocessor(train, settings)
    assert fitted.selected == ("keep",)
    assert fitted.dropped == dict(missing="all_missing", constant="constant", sparse="high_missing",
                                  near="near_constant", unsupported="unsupported")
    future = pd.DataFrame([[np.nan, 100, 500, 6, 9, 10]], columns=train.columns)
    np.testing.assert_array_equal(fitted.transform(future), [[8.]])
    assert fitted.medians == (8.,)
    with pytest.raises(ValueError, match="model columns"):
        fitted.transform(future.drop(columns=["keep"]))
    with pytest.raises(ValueError, match="model columns"):
        fitted.transform(future[future.columns[::-1]])
    with pytest.raises(ValueError, match="infinite"):
        fitted.transform(future.assign(keep=np.inf))


def test_scaled_transform_uses_imputed_training_statistics():
    train = pd.DataFrame(dict(usage=[1., 3., np.nan, 5.]))
    fitted = fit_preprocessor(train, TrainingConfig(min_observations=2), scale=True)
    assert fitted.medians == fitted.means == (3.,)
    np.testing.assert_allclose(fitted.transform(train).std(axis=0), [1.])
    np.testing.assert_array_equal(fitted.transform(pd.DataFrame(dict(usage=[np.nan]))), [[0.]])
