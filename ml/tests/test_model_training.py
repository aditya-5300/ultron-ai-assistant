import json

import pytest

from ultron_health_ml.features.config import FeatureConfig
from ultron_health_ml.models import training
from ultron_health_ml.models.config import Candidate, TrainingConfig


def test_duplicate_seeds_are_rejected():
    with pytest.raises(ValueError, match="disjoint"):
        TrainingConfig(train_seeds=(101,), validation_seeds=(101,))


def test_offline_run_enforces_split_order_and_reports(tmp_path, monkeypatch):
    output = tmp_path / "artifact"
    settings = TrainingConfig(days_per_run=0.045, train_seeds=(101,), validation_seeds=(201,), test_seeds=(301,),
                              candidates=(Candidate(n_estimators=5),))
    expected = []
    original_generate = training.generate_run
    original_select = training.select_model
    original_labels = training.load_evaluation_labels

    def generate(root, split, *args):
        expected.append(split)
        return original_generate(root, split, *args)

    def select(*args):
        model = original_select(*args)
        expected.append("frozen")
        return model

    def labels(path):
        expected.append(f"labels-{path.name}")
        return original_labels(path)

    monkeypatch.setattr(training, "generate_run", generate)
    monkeypatch.setattr(training, "select_model", select)
    monkeypatch.setattr(training, "load_evaluation_labels", labels)
    result = training.train(settings, FeatureConfig(), tmp_path / "data", output)
    assert expected == ["train", "validation", "labels-validation-201", "frozen", "test", "labels-test-301"]
    assert result["parity"]["decisions_identical"]
    assert result["methods"]["isolation_forest"]["excluded_positive_rows"] > 0
    assert result["methods"]["isolation_forest"]["workloads"]["BACKUP_WORKLOAD"]["rows"] > 0
    assert (output / "scenario_evaluation.csv").is_file()
    assert (output / "split_membership.csv").is_file()
    assert len(json.loads((output / "splits.json").read_text())) == 3
    with pytest.raises(ValueError, match="empty"):
        training.train(settings, FeatureConfig(), tmp_path / "new-data", output)
