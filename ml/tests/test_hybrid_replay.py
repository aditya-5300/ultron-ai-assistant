from pathlib import Path

import pytest

from ultron_health_ml.features.config import FeatureConfig
from ultron_health_ml.hybrid.config import load_settings
from ultron_health_ml.hybrid.datasets import empty_directory, open_events, open_telemetry, prepare, verify_lock
from ultron_health_ml.hybrid import replay
from ultron_health_ml.hybrid.replay import run_replay, trace_sha
from ultron_health_ml.models.datasets import sha256


@pytest.fixture(scope="module")
def inputs(tmp_path_factory):
    root = tmp_path_factory.mktemp("hybrid-lock")
    path = Path(__file__).resolve().parents[1] / "configs/hybrid_detection.yaml"
    config = load_settings(path).model_copy(update=dict(
        train_seeds=(5101,), validation_seeds=(5102,), holdout_seeds=(5103,), hours_per_run=4.,
    ))
    lock = prepare(root, config)
    return root, config, lock, sha256(root / "locked_inputs.json")


def test_lock_disjoint_labels_and_reproducible_replay(inputs):
    root, config, lock, digest = inputs
    assert verify_lock(root, config, digest) == lock
    assert [run["split"] for run in lock["runs"]] == ["train", "validation", "holdout"]
    run = lock["runs"][2]
    label_path = root / run["run"] / "events.json"
    data = label_path.read_bytes()
    label_path.write_bytes(b"tampered")
    try:
        source = open_telemetry(root, run)
        with pytest.raises(ValueError, match="locked label"):
            open_events(root, run)
        assert scored_hash(source, run, config) != trace_sha({})
    finally:
        label_path.write_bytes(data)
    assert scored_hash(source, run, config) == scored_hash(source, run, config)
    assert open_events(root, run)
    with pytest.raises(ValueError, match="checksum changed"):
        verify_lock(root, config, "bad")


def scored_hash(source, run, config):
    traces = run_replay(source, run["run"], config, FeatureConfig(), (("hybrid", 1.),))
    return trace_sha({key: [value] for key, value in traces.items()})


def test_selection_is_frozen_before_holdout_is_opened(inputs, tmp_path, monkeypatch):
    root, config, _, digest = inputs
    output = tmp_path / "report"
    accessed = []

    def tracked_telemetry(path, run):
        if run["split"] == "holdout":
            assert (output / "frozen_selection.json").is_file()
        accessed.append(("telemetry", run["split"]))
        return open_telemetry(path, run)

    def tracked_labels(path, run):
        assert accessed[-1] == ("telemetry", run["split"])
        accessed.append(("labels", run["split"]))
        return open_events(path, run)

    monkeypatch.setattr(replay, "open_telemetry", tracked_telemetry)
    monkeypatch.setattr(replay, "open_events", tracked_labels)
    decision, digest_of_decisions = replay.evaluate_locked(root, output, config, digest, {})
    assert accessed == [(stage, split) for split in ("train", "validation", "holdout")
                        for stage in ("telemetry", "labels")]
    assert decision in {"REPEAT PHASE 4.5", "GO TO PHASE 5 WITH LIMITATIONS"}
    assert len(digest_of_decisions) == 64


def test_never_write_below_frozen_output():
    repo = Path(__file__).resolve().parents[2]
    with pytest.raises(ValueError, match="read-only"):
        empty_directory(repo / "ml/artifacts/isolation_forest/would-create")
