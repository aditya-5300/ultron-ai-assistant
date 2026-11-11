from dataclasses import replace
import json

import numpy as np
import pandas as pd
import pytest

from ultron_health_ml.models.artifact import load_model_artifact, save_model_artifact, seal
from ultron_health_ml.models.config import Candidate, TrainingConfig
from ultron_health_ml.models.isolation_forest import FeatureSignature, fit_model


@pytest.fixture
def bundle(tmp_path):
    signature = FeatureSignature("1.0.0", "1.0.0", "real_ultron", "fixture", ("cpu", "mem"))
    frame = pd.DataFrame(dict(cpu=np.linspace(0, 1, 50), mem=np.linspace(1, 2, 50)))
    model = replace(fit_model(frame.iloc[:40], signature, TrainingConfig(), Candidate(n_estimators=10)), threshold=0.55)
    save_model_artifact(tmp_path, model, {}, {}, "{}\n")
    return tmp_path, model, frame.iloc[40:]


def test_round_trip_and_trust_gate(bundle):
    path, model, held_out = bundle
    with pytest.raises(ValueError, match="trusted"):
        load_model_artifact(path, model.signature)
    reloaded = load_model_artifact(path, model.signature, trusted=True)
    before = model.score_frame(held_out, model.signature)
    after = reloaded.score_frame(held_out, model.signature)
    np.testing.assert_allclose(before, after, atol=1e-12, rtol=0)
    np.testing.assert_array_equal(before >= model.threshold, after >= reloaded.threshold)


@pytest.mark.parametrize(("file", "content", "reseal", "error"), [
    ("model.joblib", b"damaged", False, "checksum"),
    ("model.joblib", b"damaged", True, "invalid model payload"),
    ("selected_features.json", b'["mem", "cpu"]', True, "selected features"),
    ("evaluation.json", None, False, "incomplete"),
])
def test_rejects_incompatible_payload(bundle, file, content, reseal, error):
    path, model, _ = bundle
    if content is None:
        (path / file).unlink()
    else:
        (path / file).write_bytes(content)
    if reseal:
        seal(path)
    with pytest.raises(ValueError, match=error):
        load_model_artifact(path, model.signature, trusted=True)


def test_version_profile_schema_and_fingerprint(bundle):
    path, model, _ = bundle
    for change in (dict(feature_profile="synthetic_extended"), dict(feature_schema_version="2"),
                   dict(feature_config_fingerprint="bad"), dict(model_columns=("mem", "cpu"))):
        with pytest.raises(ValueError, match="signature"):
            load_model_artifact(path, replace(model.signature, **change), trusted=True)
    metadata_path = path / "metadata.json"
    metadata = json.loads(metadata_path.read_text())
    metadata["model_version"] = "9.0.0"
    metadata_path.write_text(json.dumps(metadata))
    seal(path)
    with pytest.raises(ValueError, match="unsupported model version"):
        load_model_artifact(path, model.signature, trusted=True)
