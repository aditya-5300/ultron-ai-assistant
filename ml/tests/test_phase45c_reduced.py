from pathlib import Path
from types import SimpleNamespace

import pytest

from feature_helpers import at
from ultron_health_ml.hybrid.config import load_settings
from ultron_health_ml.hybrid.datasets import Event, PROTOCOL, settings_digest
from ultron_health_ml.hybrid.detector import Episode
from ultron_health_ml.hybrid.evaluation import Trace
from ultron_health_ml.models.artifact import write_json
from ultron_health_ml.models.datasets import sha256
from ultron_health_ml.reduced import replay
from ultron_health_ml.reduced.datasets import new_output, open_lock, protocol
from ultron_health_ml.reduced.policy import ReducedReplay, common_signals
from ultron_health_ml.reduced.protocol import METHODS, POINTS, Point, choose


@pytest.fixture
def config():
    return load_settings(Path(__file__).resolve().parents[1] / "configs/hybrid_detection.yaml")


def frame(seconds, memory=90., disk=35., process=0., cpu=30., battery=5.):
    values = {}
    for key, value in dict(cpu_usage_pct=cpu, disk_free_pct=disk, memory_usage_pct=memory,
                           selected_process_nonresponding_count=process, battery_pct=battery,
                           battery_capacity_pct=45., disk_temperature_c=95., relevant_event_count=20.).items():
        values.update({f"{key}__current": value, f"{key}__fresh": 1, f"{key}__observation_age_seconds": 0.})
    return SimpleNamespace(sample_time=at(seconds), values=values, window_quality={},
                           sampling_gap=False, context_changed=False, ac_online=False)


@pytest.mark.parametrize("method", METHODS)
def test_reduced_scope_and_independent_critical_pass(config, method):
    policy = ReducedReplay(config, Point(method, .85, 1))
    for stamp in range(0, 1200, 30):
        row = frame(stamp)
        policy.consume(row, lambda: common_signals(row))
    assert not policy.episodes
    row = frame(1200, memory=95., disk=4.)

    def signals():
        assert policy.open["memory"].critical_at == at(1200).timestamp()
        assert policy.open["disk_capacity"].critical_at == at(1200).timestamp()
        return {}

    _, flags = policy.consume(row, signals)
    assert flags == {"memory", "disk_capacity"}
    assert policy.safety_misses == 0
    assert {episode.severity for episode in policy.episodes} == {"critical"}


def test_entry_and_recovery_are_shared_for_equivalent_scores(config):
    policies = [ReducedReplay(config, Point(method, 1, 1)) for method in METHODS]
    for stamp in range(0, 550, 30):
        active = 0.9 if stamp < 120 else 1.3
        for policy in policies:
            policy.consume(frame(stamp), lambda: {"process": dict.fromkeys(METHODS, active)})
    assert all(len(policy.silent) == len(policy.episodes) == 1 for policy in policies)
    assert {policy.episodes[0].opened_at for policy in policies} == {at(420).timestamp()}
    for stamp in range(570, 790, 30):
        for policy in policies:
            policy.consume(frame(stamp), dict)
    assert len({policy.episodes[0].closed_at for policy in policies}) == 1
    assert policies[0].episodes[0].closed_at is not None


def summary(rate=0., recall=.7, delay=100.):
    return dict(false_episodes_per_day=rate, workload_nuisance_rate=0., critical_expected=1,
                safety_misses=0, critical_missed=0, critical_delayed=0, statistical_failures=0,
                event_recall=recall, detected=int(recall * 100), median_delay_seconds=delay, p95_delay_seconds=delay)


def test_frontier_selects_best_qualified_and_keeps_overbudget_diagnostic(config):
    points = tuple(point for point in POINTS if point.method == "fixed")
    development = {point.name: summary(.1, .5) for point in points}
    validation = {point.name: summary(.25, .5) for point in points}
    validation[points[0].name] = summary(.6, 1.)
    validation[points[1].name] = summary(.2, .7)
    validation[points[2].name] = summary(.2, .7, 0.)
    picked, qualified, closest = choose(points, development, validation, config)
    assert qualified and picked == points[2] and closest == points[0]
    assert choose(points, validation, {point.name: summary(.8) for point in points}, config)[1] is False


def test_paired_noninferiority_and_inconclusive():
    rows = [dict(run=f"run-{index}", event_id=system, subsystem=system, in_primary_scope=True,
                 detected=True, delay_seconds=130.) for index in range(12)
            for system in ("cpu", "process", "disk_capacity")]
    identical = replay.paired(rows, rows)
    assert identical["value"] == "FAIL" and identical["simpler_descriptively_noninferior"]
    improved = replay.paired([{**item, "delay_seconds": 90.} for item in rows], rows)
    assert improved["value"] == "PASS" and not improved["simpler_descriptively_noninferior"]
    assert replay.paired(rows[:3], rows[:3])["value"] == "INCONCLUSIVE"


def test_acceptance_requires_qualified_value_and_prefers_noninferior_simpler_method(config):
    chosen = {method: Point(method, 1., 1.) for method in METHODS}
    qualified = dict.fromkeys(METHODS, True)
    rows = [dict(run=f"run-{index}", event_id=system, subsystem=system, in_primary_scope=True,
                 detected=True, delay_seconds=150.) for index in range(12)
            for system in ("cpu", "process", "disk_capacity")]
    results = {point.name: (summary(recall=1.), rows) for point in chosen.values()}
    assert replay.assess(chosen, qualified, results, config, True, 2)[2] == "STOP PREDICTIVE PIPELINE"
    results[chosen["hybrid"].name] = summary(recall=1.), [{**item, "delay_seconds": 100.} for item in rows]
    assert replay.assess(chosen, qualified, results, config, True, 2)[2] == "GO TO PHASE 5 WITH REDUCED SUBSYSTEM SCOPE"
    qualified = {method: method == "hybrid" for method in METHODS}
    assert replay.assess(chosen, qualified, results, config, True, 2)[2] == "REPEAT PHASE 4.5"


def test_primary_matching_excludes_ram_recall_but_retains_critical_safety(config):
    events = [Event("cpu", "cpu_contention", "cpu", 0, 80, 20, None, 90, "warning", (), 1.),
              Event("ram", "memory_critical", "memory", 80, 160, 100, 120, 170, "critical", (), 1.)]
    ram = Episode("ram", "memory", "pressure", 120, 120, "critical", critical_at=120)
    early_cpu = Episode("cpu", "cpu", "pressure", 10, 40, "warning")
    trace = Trace("run", [0, 30, 120, 180], [set(), {"cpu"}, {"memory"}, set()],
                  [{"cpu": True, "process": True, "disk_capacity": True}] * 4,
                  {"memory": [120]}, [early_cpu, ram], 86400, 1, 0, 0)
    result, cases, *_ = replay.scoped({"fixed": [trace]}, {"fixed": []}, {"run": events}, config)["fixed"]
    assert result["events"] == 1 and result["detected"] == 0 and result["global_detected"] == 1
    assert result["false_warning_episodes"] == 1
    assert result["critical_expected"] == 1 and result["critical_missed"] == result["critical_delayed"] == 0
    assert [row["in_primary_scope"] for row in cases] == [True, False]


def test_lock_rejects_resealed_scope_changes_and_prior_paths(config, tmp_path):
    config = config.model_copy(update=dict(train_seeds=(7101,), validation_seeds=(7201,), holdout_seeds=(7301,)))
    repo = Path(__file__).resolve().parents[2]
    with pytest.raises(ValueError, match="read-only"):
        new_output(repo / "ml/artifacts/phase45b/verified-v1/do-not-write")
    runs = [dict(run=f"{split}-{seed}", split=split, seed=seed, sha256={"telemetry.csv": str(seed)})
            for split, seed in (("train", 7101), ("validation", 7201), ("holdout", 7301))]
    spec = protocol()
    manifest = dict(protocol=PROTOCOL, experiment=spec, settings_sha256=settings_digest(config), runs=runs)
    path = tmp_path / "locked_inputs.json"
    write_json(path, manifest)
    assert open_lock(tmp_path, config, sha256(path))["runs"] == runs
    manifest["experiment"] = {**spec, "scope": ("memory",)}
    write_json(path, manifest)
    with pytest.raises(ValueError, match="differs from the locked version"):
        open_lock(tmp_path, config, sha256(path))


def test_holdout_opens_once_after_points_are_frozen(config, tmp_path, monkeypatch):
    runs = [dict(run=split, split=split) for split in ("train", "validation", "holdout")]
    output = tmp_path / "report"
    accessed = []

    def partition(_, split, __, points, copies=1):
        name = split[0]["split"]
        accessed.append(name)
        if name == "holdout":
            assert (output / "frozen_selection.json").is_file()
            assert copies == 2 and len(points) == 4
        mock = {point.name: (summary(), [], [], {}, []) for point in points}
        return [mock] * copies, [{}] * copies, {}, {point.name: [1.] for point in points}

    monkeypatch.setattr(replay, "open_lock", lambda *_: {"runs": runs, "experiment": {}})
    monkeypatch.setattr(replay, "partition", partition)
    monkeypatch.setattr(replay, "scoped", lambda traces, *_: traces)
    monkeypatch.setattr(replay, "decision_hash", lambda *_: "identical")
    monkeypatch.setattr(replay, "assess", lambda *_: ({}, [], "REPEAT PHASE 4.5"))
    assert replay.run(tmp_path / "inputs", output, config, "fixture", {})[1] == "identical"
    assert accessed == ["train", "validation", "holdout"]
    assert (output / "comparator_operating_points.csv").read_text().count("\n") == 4
