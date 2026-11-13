from pathlib import Path
from types import SimpleNamespace

import pytest

from feature_helpers import at, telemetry
from ultron_health_ml.features.engineering import iter_features
from ultron_health_ml.forecast import replay as pipeline
from ultron_health_ml.forecast.baselines import FEATURES, History, Signal, UNKNOWN, fit_noise, measurement, scores
from ultron_health_ml.forecast.contract import Actionable, Event, METRICS, read_experiment, read_targets
from ultron_health_ml.forecast.datasets import experiment_definition, open_lock, output_directory
from ultron_health_ml.forecast.evaluation import budget, choose, covered, future_label, score_run
from ultron_health_ml.forecast.lifecycle import Lifecycle, Point
from ultron_health_ml.models.artifact import write_json
from ultron_health_ml.models.datasets import sha256


@pytest.fixture
def targets():
    return read_targets(Path(__file__).resolve().parents[1] / "configs/forecast_target_contract.json")


@pytest.fixture
def settings():
    return read_experiment(Path(__file__).resolve().parents[1] / "configs/phase5_config.yaml")


def data(second, *, cpu=42., ram=67., disk=40., process=0.):
    return second, dict(zip(METRICS, (cpu, ram, disk, process), strict=True))


def source(samples):
    return SimpleNamespace(window=telemetry(samples), dataset_version="fixture")


def test_targets_require_independent_sustained_context_and_fresh_disk(targets):
    cpu = Actionable("cpu", targets.targets["cpu"])
    for stamp in range(0, 390, 30):
        assert cpu.update(stamp, data(stamp, cpu=96.)[1], stamp, True) is None
    for stamp in range(390, 690, 30):
        assert cpu.update(stamp, data(stamp, cpu=96., ram=88.)[1], stamp, True) is None
    assert cpu.update(690, data(690, cpu=96., ram=88.)[1], 690, True) == "actionable"
    process = Actionable("process", targets.targets["process"])
    for stamp in range(0, 360, 30):
        assert process.update(stamp, data(stamp, process=4.)[1], stamp, True) is None
    assert process.update(360, data(360, process=4.)[1], 360, True) == "actionable"
    disk = Actionable("disk_capacity", targets.targets["disk_capacity"])
    assert disk.update(330, data(330, disk=9.)[1], 0, False) is None
    assert disk.update(360, data(360, disk=10.)[1], 360, True) == "actionable"


def test_future_mutations_do_not_change_phase3_prefix_or_predictions(targets):
    prefix = [data(index * 30, process=2.) for index in range(18)]
    left = source(prefix + [data(540, process=1.)])
    right = source(prefix + [data(540, cpu=94., disk=5., process=6.)])
    assert [row.model_features() for row in list(iter_features(left.window, FEATURES))[:-1]] == [
        row.model_features() for row in list(iter_features(right.window, FEATURES))[:-1]]
    points = (Point("process", "persistence", 600, .65),)
    noise = dict(cpu=.5, disk_capacity=.12, process=.15)
    run = {"run": "fixture"}
    common = []
    for full in (left, right):
        past = full.window.model_copy(update=dict(records=full.window.records[:-1],
                                                  window_end=at(510), as_of=at(510)))
        common.append(pipeline.replay(run, SimpleNamespace(window=past, dataset_version="fixture"),
                                     targets, noise, points, measure=True))
    assert common[0][:4] == common[1][:4]
    episode = common[0][0][points[0].name]["episodes"][0]
    assert episode.first_prediction <= at(510).timestamp()
    assert episode.detection_at is None
    assert future_label(episode.first_prediction, 600, [Event("later", "process", at(550).timestamp(),
                         at(800).timestamp())])[0] == "future"
    assert future_label(episode.first_prediction, 600, [Event("later", "process", at(2000).timestamp(),
                         at(2600).timestamp())])[0] == "negative"


def test_cached_timestamp_and_disk_eta_require_quality_span_and_direction(targets):
    target = targets.targets["disk_capacity"]
    samples = [data(0, disk=28.), data(30, disk=(28., 0, "cached"))]
    readings = source(samples).window
    feature = list(iter_features(readings, FEATURES))[1]
    assert measurement(readings.records[1], feature, target) == (at(0).timestamp(), False)
    track = Actionable("disk_capacity", target)
    history = History("disk_capacity")
    assert scores("disk_capacity", target, data(0, disk=30.)[1], track, history, .12)["eta"] == UNKNOWN
    for stamp in range(0, 1500, 300):
        history.add(stamp, 30. - stamp / 120., target.max_gap_seconds)
    history.add(1200, 3., target.max_gap_seconds)
    assert len(history.points) == 5
    signal = scores("disk_capacity", target, data(1200, disk=20.)[1], track, history, .12)["eta"]
    assert signal.score == pytest.approx(1.) and signal.eta_seconds == pytest.approx(1200.)
    history.add(2500, 28., target.max_gap_seconds)
    assert scores("disk_capacity", target, data(2500, disk=28.)[1], track, history, .12)["eta"] == UNKNOWN


def test_noise_preprocessing_cannot_fit_validation_or_holdout(targets):
    samples = source([data(0), data(30, cpu=45.)])
    floors = fit_noise([({"split": "development"}, samples)], targets)
    assert floors["cpu"] == 3.
    for split in ("validation", "holdout"):
        with pytest.raises(ValueError, match="development telemetry only"):
            fit_noise([({"split": split}, samples)], targets)


def test_process_forecast_persistence_duplicate_suppression_and_detection_ownership(targets):
    point = Point("process", "persistence", 600, .65)
    machine = Lifecycle("fixture", point, targets.targets["process"], targets.version)
    for stamp in range(0, 390, 30):
        machine.step(stamp, stamp, True, 2., Signal(.95), None)
    assert len(machine.episodes) == 1
    assert machine.episodes[0].first_prediction == 60
    assert machine.raw == 13 and machine.suppressed == 10
    machine.step(390, None, False, None, UNKNOWN, None)
    assert machine.owner is machine.episodes[0]
    machine.step(420, 420, True, 4., Signal(0.), "actionable")
    assert machine.interactions[0]["advisory_interruptions"] == 0
    assert machine.episodes[0].state == "detected"
    for stamp in range(450, 600, 30):
        machine.step(stamp, stamp, True, 0., Signal(0.), "recovered" if stamp == 450 else None)
    assert machine.episodes[0].closed_at == 570
    for stamp in range(600, 840, 30):
        machine.step(stamp, stamp, True, 2., Signal(.95), None)
    assert len(machine.episodes) == 2 and machine.episodes[0].forecast_id != machine.episodes[1].forecast_id


def test_cancellation_and_strict_horizon_minimum_lead(targets):
    target = targets.targets["process"]
    point = Point("process", "linear", 600, .65)
    machine = Lifecycle("fixture", point, target, targets.version)
    for stamp in (0, 30, 60):
        machine.step(stamp, stamp, True, 2., Signal(.9, 540), None)
    forecast = machine.episodes[0]
    at_end = Event("boundary", "process", 660., 990.)
    assert future_label(60, 600, [at_end]) == ("future", at_end)
    assert future_label(660, 600, [at_end]) == ("late", None)
    assert future_label(59, 600, [at_end]) == ("negative", None)
    trace = dict(run="fixture", episodes=[forecast], detections=[680], usable=list(range(0, 1000, 30)),
                 finish=990, interactions=[], critical=[])
    hit = score_run(point, target, trace, ([at_end], []))[1][0]
    assert hit["evaluation"] == "confirmed" and hit["minimum_useful_met"] and hit["lead_seconds"] == 600
    short = Event("short", "process", 120., 900.)
    assert not score_run(point, target, trace, ([short], []))[1][0]["minimum_useful_met"]
    for stamp in (90, 120, 150, 180, 210):
        machine.step(stamp, stamp, True, 0., Signal(0.), None)
    assert forecast.cancelled_at == 210
    assert score_run(point, target, trace, ([at_end], []))[1][0]["evaluation"] == "cancelled_false"
    assert not covered(60, 660, [60, 650], 990, target.max_gap_seconds)


def test_selection_uses_two_earlier_partitions_and_budget(settings):
    quiet, loud = Point("cpu", "linear", 600, .85), Point("cpu", "robust", 900, .65)
    def metrics(rate, recall):
        return (dict(false_per_day=rate, workload_nuisance=0., observable_events=20, useful_recall=recall,
                     episode_precision=.9, median_lead_seconds=270.),)
    dev = {quiet.name: metrics(.1, .5), loud.name: metrics(.8, 1.)}
    validation = {quiet.name: metrics(.1, .5), loud.name: metrics(0., 1.)}
    assert choose((quiet, loud), dev, validation, settings) == quiet
    assert not budget({**dev[quiet.name][0], "workload_nuisance": .06}, settings)


def test_holdout_truth_opens_only_after_both_scores(targets, monkeypatch):
    point = Point("cpu", "linear", 600, .65)
    calls = []
    def read(*_):
        calls.append("telemetry")
        return "fixture"
    def replay(*_, **__):
        calls.append("score")
        return {point.name: {}}, [0], {}, "digest", []
    def truth(*_):
        assert calls == ["telemetry", "score", "score"]
        calls.append("truth")
        return [], []
    monkeypatch.setattr(pipeline, "telemetry", read)
    monkeypatch.setattr(pipeline, "replay", replay)
    monkeypatch.setattr(pipeline, "labels", truth)
    monkeypatch.setattr(pipeline, "evaluate", lambda *_: {})
    pipeline.scored(Path("."), [{"run": "holdout-8301"}], targets, {}, (point,), copies=2)
    assert calls[-1] == "truth"


def test_horizons_and_thresholds_are_written_before_holdout(settings, targets, monkeypatch, tmp_path):
    runs = [dict(run=split, split=split) for split in ("development", "validation", "holdout")]
    output = tmp_path / "report"
    access = []
    summary = dict(false_per_day=.1, workload_nuisance=0., observable_events=12, useful_recall=.5,
                   episode_precision=.9, median_lead_seconds=300.)

    def scored(_, group, __, ___, points, **kwargs):
        split = group[0]["split"]
        access.append(split)
        if split == "holdout":
            assert (output / "horizon_selection.json").is_file()
            assert kwargs["copies"] == 2
            raise RuntimeError("holdout boundary reached")
        return ([{point.name: (summary,) for point in points}],)

    monkeypatch.setattr(pipeline, "open_lock", lambda *_: {"runs": runs})
    monkeypatch.setattr(pipeline, "telemetry", lambda *_: None)
    monkeypatch.setattr(pipeline, "fit_noise", lambda *_: {})
    monkeypatch.setattr(pipeline, "scored", scored)
    with pytest.raises(RuntimeError, match="holdout boundary reached"):
        pipeline.run(tmp_path / "inputs", output, settings, targets, "fixture", {})
    assert access == ["development", "validation", "holdout"]


def test_expired_process_notice_cannot_reopen_without_recovery(targets):
    point = Point("process", "robust", 300, .65)
    machine = Lifecycle("fixture", point, targets.targets["process"], targets.version)
    for stamp in (0, 30, 60):
        machine.step(stamp, stamp, True, 2., Signal(.9), None)
    machine.step(390, None, False, None, UNKNOWN, None)
    assert machine.owner.state == "expired" and machine.owner.closed_at is None
    for stamp in (420, 450, 480):
        machine.step(stamp, stamp, True, 2., Signal(.9), None)
    assert len(machine.episodes) == 1
    machine.step(510, 510, True, 4., Signal(0.), "actionable")
    assert machine.interactions[-1]["advisory_interruptions"] == 1


def test_replay_digest_includes_raw_predictions_and_acceptance():
    base = pipeline.digest_decisions({"episodes": []}, ["raw"], [], {"passed": True})
    assert base == pipeline.digest_decisions({"episodes": []}, ["raw"], [], {"passed": True})
    assert base != pipeline.digest_decisions({"episodes": []}, ["other"], [], {"passed": True})
    assert base != pipeline.digest_decisions({"episodes": []}, ["raw"], [], {"passed": False})


def test_resealed_lock_rejects_target_changes_and_protects_previous_exports(tmp_path, settings, targets):
    config = settings.model_copy(update=dict(development_seeds=(8101,), validation_seeds=(8201,), holdout_seeds=(8301,)))
    runs = [dict(run=f"{split}-{seed}", split=split, seed=seed, sha256={"telemetry.csv": str(seed)})
            for split, seed in (("development", 8101), ("validation", 8201), ("holdout", 8301))]
    manifest = {"definition": experiment_definition(config, targets), "runs": runs}
    path = tmp_path / "locked_inputs.json"
    write_json(path, manifest)
    assert open_lock(tmp_path, config, targets, sha256(path))["runs"] == runs
    manifest["definition"]["targets"]["targets"]["cpu"]["boundary"] = 90
    write_json(path, manifest)
    with pytest.raises(ValueError, match="contract changed"):
        open_lock(tmp_path, config, targets, sha256(path))
    with pytest.raises(ValueError, match="read-only"):
        output_directory(Path(__file__).resolve().parents[2] / "ml/generated/phase45c/locked-v1/unsafe")


def test_failed_forecast_scoring_does_not_interfere_with_safety(targets, monkeypatch):
    def fail(*_):
        raise RuntimeError("forecast unavailable")
    monkeypatch.setattr(pipeline, "scores", fail)
    samples = source([data(0, ram=96., disk=4.), data(30, ram=96., disk=(4., 0, "cached"))])
    result = pipeline.replay({"run": "fixture"}, samples, targets, dict(cpu=.5, disk_capacity=.12, process=.15),
                            (Point("disk_capacity", "eta", 1800, .85),))
    assert result[2]["checks"] == 4 and result[2]["mismatches"] == 0
    assert result[2]["scorer_failures"] == 6
    assert len(result[2]["openings"]["disk_capacity"]) == 1
