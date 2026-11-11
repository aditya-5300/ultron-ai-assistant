from pathlib import Path
from types import SimpleNamespace

import pytest

from feature_helpers import at
from ultron_health_ml.hybrid.config import load_settings
from ultron_health_ml.hybrid.datasets import PROTOCOL, settings_digest
from ultron_health_ml.models.artifact import write_json
from ultron_health_ml.models.datasets import sha256
from ultron_health_ml.nuisance import policy
from ultron_health_ml.nuisance.datasets import contract, locked, new_output, variation
from ultron_health_ml.nuisance.protocol import Experiment, objective


@pytest.fixture
def settings():
    return load_settings(Path(__file__).resolve().parents[1] / "configs/hybrid_detection.yaml")


def row(seconds, *, memory=60, cpu=30, process=0, disk=40, age=0, growth=None, fit=None):
    values = {}
    for metric, value, reading_age in (
        ("memory_usage_pct", memory, 0), ("cpu_usage_pct", cpu, 0),
        ("selected_process_nonresponding_count", process, 0), ("disk_free_pct", disk, age),
    ):
        if value is not None:
            values.update({f"{metric}__current": value, f"{metric}__fresh": int(reading_age == 0),
                           f"{metric}__observation_age_seconds": reading_age})
    values.update(memory_usage_pct__delta_300s=growth, memory_usage_pct__slope_per_hour_300s=growth,
                  memory_usage_pct__trend_r2_300s=fit)
    return SimpleNamespace(sample_time=at(seconds), values=values, context_changed=False, sampling_gap=False,
                           ac_online=False, window_quality={})


def test_stable_high_memory_not_a_warning_and_critical_is_immediate(settings, monkeypatch):
    monkeypatch.setattr(policy, "signals", lambda *_: {"sustained": 6.})
    detector = policy.PolicyReplay(settings, "core")
    for seconds in range(0, 451, 30):
        detector.step(row(seconds, memory=86, cpu=82, growth=0, fit=0))
    assert not any(event.subsystem == "memory" for event in detector.episodes)
    _, active = detector.step(row(480, memory=95, cpu=82, growth=0, fit=0, disk=4, age=30))
    assert {"memory", "disk_capacity"} <= active
    assert detector.open["memory"].critical_at == detector.open["disk_capacity"].critical_at == at(480).timestamp()
    assert detector.open["disk_capacity"].fresh_observations == 0


def test_build_context_requires_stronger_memory_evidence(settings, monkeypatch):
    monkeypatch.setattr(policy, "signals", lambda *_: {"sustained": 3.})
    normal = policy.PolicyReplay(settings, "strong_memory")
    busy = policy.PolicyReplay(settings, "strong_memory")
    for seconds in range(0, 361, 30):
        normal.step(row(seconds, memory=81, cpu=42, growth=3, fit=.9))
        busy.step(row(seconds, memory=81, cpu=83, growth=3, fit=.9))
    assert normal.open["memory"].severity == "warning"
    assert "memory" not in busy.open


def test_coincident_process_evidence_is_one_warning(settings):
    detector = policy.PolicyReplay(settings, "core")
    for seconds in range(0, 541, 30):
        detector.step(row(seconds, cpu=91, process=5))
    assert set(detector.open) == {"cpu"}
    assert detector.associations == 1
    assert "associated_process_nonresponse" in detector.open["cpu"].detector_sources
    assert "cause unknown" in detector.open["cpu"].evidence["supporting_signal"]


def test_short_process_stall_is_observe_only_and_long_stall_warns(settings):
    detector = policy.PolicyReplay(settings, "strong_process")
    for seconds in range(0, 121, 30):
        detector.step(row(seconds, process=4))
    detector.step(row(150))
    assert len(detector.silent) == 1 and not detector.episodes
    for seconds in range(180, 541, 30):
        detector.step(row(seconds, process=5))
    assert len(detector.episodes) == 1 and detector.episodes[0].subsystem == "process"


def test_statistical_failure_cannot_override_safety(settings, monkeypatch):
    def broken(*_):
        raise RuntimeError("fixture failure")

    monkeypatch.setattr(policy, "signals", broken)
    detector = policy.PolicyReplay(settings, "core")
    _, active = detector.step(row(0, memory=96, disk=4))
    assert {"memory", "disk_capacity"} <= active and detector.safety_misses == 0


def test_budget_precedes_recall_and_critical_precedes_budget(settings):
    def result(rate, recall, misses=0):
        return dict(false_episodes_per_day=rate, workload_nuisance_rate=0., event_recall=recall,
                    monitored_device_days=5., flap_count=0, duplicate_advisories=0,
                    median_delay_seconds=90., critical_missed=misses, critical_delayed=0,
                    safety_misses=0, false_critical_episodes=0)

    assert objective(result(0, .5), settings) > objective(result(.8, 1), settings)
    assert objective(result(.8, .5), settings) > objective(result(0, 1., misses=1), settings)


def test_new_seed_policy_and_frozen_output_guard():
    with pytest.raises(ValueError, match="three distinct"):
        Experiment(protocol="phase45b-v1", development_seeds=(6100,), validation_seeds=(6200,),
                   holdout_seeds=(6200,), hours_per_run=4)
    repo = Path(__file__).resolve().parents[2]
    with pytest.raises(ValueError, match="read-only"):
        new_output(repo / "ml/generated/phase45/locked-v1/do-not-write")
    event = {"event_start": 10., "event_end": 1200., "kind": "build"}
    assert variation("memory_usage_pct", 300., event, 5) > variation("memory_usage_pct", 300., event, 1)
    assert variation("selected_process_nonresponding_count", 140., event, 5) > 0
    assert variation("selected_process_nonresponding_count", 500., event, 5) == 0


def test_lock_rejects_a_different_objective_even_if_input_manifest_is_resealed(settings, tmp_path):
    config = settings.model_copy(update=dict(train_seeds=(6101,), validation_seeds=(6201,), holdout_seeds=(6301,)))
    policy_contract = contract()
    runs = [dict(run=f"{split}-{seed}", split=split, seed=seed, sha256={"telemetry.csv": str(seed)})
            for split, seed in (("train", 6101), ("validation", 6201), ("holdout", 6301))]
    path = tmp_path / "locked_inputs.json"
    manifest = dict(protocol=PROTOCOL, policy=policy_contract, settings_sha256=settings_digest(config), runs=runs)
    write_json(path, manifest)
    assert locked(tmp_path, config, sha256(path))["runs"] == runs
    manifest["policy"] = {**policy_contract, "objective": {"utility": "changed"}}
    write_json(path, manifest)
    with pytest.raises(ValueError, match="changed after data lock"):
        locked(tmp_path, config, sha256(path))
