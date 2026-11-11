from pathlib import Path
from types import SimpleNamespace

import pytest

from feature_helpers import at, telemetry
from ultron_health_ml.features.config import FeatureConfig
from ultron_health_ml.features.engineering import iter_features
from ultron_health_ml.hybrid.config import load_settings
from ultron_health_ml.hybrid.detector import Replay
from ultron_health_ml.hybrid.signals import ShiftState, signals


@pytest.fixture
def config():
    path = Path(__file__).resolve().parents[1] / "configs/hybrid_detection.yaml"
    original = load_settings(path)
    subsystems = {key: original.subsystems[key].model_copy(update=dict(
        min_observations=3, min_duration=60, recovery_seconds=60, max_observation_gap=140,
    )) for key in ("memory", "disk_capacity")}
    return original.model_copy(update=dict(subsystems=subsystems, missing_grace_seconds=40))


def frame(time, memory=None, disk=None, *, disk_age=0, gaps=False):
    data = {}
    for metric, value, age in (("memory_usage_pct", memory, 0), ("disk_free_pct", disk, disk_age)):
        if value is not None:
            data.update({f"{metric}__current": value, f"{metric}__observation_age_seconds": age,
                         f"{metric}__fresh": int(age == 0)})
    return SimpleNamespace(sample_time=at(time), sampling_gap=gaps, context_changed=False, ac_online=True,
                           values=data, window_quality={})


def broken(*_):
    raise RuntimeError("injected detector outage")


@pytest.mark.parametrize("variant", ["hybrid", "mad", "ewma", "cusum", "sustained", "fixed_budget", "mad_budget"])
def test_critical_independent_of_statistics_optional_ml_and_other_sensors(config, variant):
    replay = Replay(config, variant, 2., scorer=broken, optional=broken)
    replay.step(frame(0, memory=72))
    _, flagged = replay.step(frame(30, disk=4, disk_age=30))
    assert flagged == {"disk_capacity"}
    assert replay.episodes[0].critical_at == at(30).timestamp()
    assert replay.episodes[0].fresh_observations == 0
    _, flagged = replay.step(frame(60, memory=95))
    assert flagged == {"memory"}
    assert replay.open["memory"].critical_at == at(60).timestamp()
    assert replay.safety_checks == 2
    assert replay.safety_misses == 0


def test_distinct_fresh_observations_fusion_and_upgrade(config):
    replay = Replay(config, scorer=lambda *_: {"mad": 1.5, "sustained": 1.2}, optional=broken)
    for time, age in ((0, 0), (30, 30), (60, 60), (90, 90), (120, 0), (150, 30)):
        replay.step(frame(time, disk=17, disk_age=age))
        assert not replay.episodes
    replay.step(frame(180, disk=17))
    episode = replay.open["disk_capacity"]
    assert episode.opened_at == at(180).timestamp()
    assert episode.distinct_observations == episode.fresh_observations == 3
    assert episode.evidence_duration == 180
    assert episode.detector_sources == {"mad", "sustained"}
    replay.step(frame(195, disk=4))
    assert len(replay.episodes) == 1
    assert episode.severity == "critical"
    assert episode.critical_at == at(195).timestamp()
    assert episode.detector_sources == {"mad", "sustained", "safety"}


def test_hysteresis_missing_and_recovery_require_valid_elapsed_evidence(config):
    replay = Replay(config, scorer=lambda *args: {"mad": (args[4] - 68) / 12})
    for time, value in [(0, 83), (30, 84), (60, 83), (90, 78), (120, 78), (150, None)]:
        replay.step(frame(time, memory=value))
    episode = replay.open["memory"]
    assert episode.current_state == "insufficient_telemetry"
    assert episode.closed_at is None
    replay.step(frame(180, memory=70))
    replay.step(frame(210, memory=70))
    replay.step(frame(240, memory=70, gaps=True))
    assert episode.closed_at is None
    replay.step(frame(270, memory=70))
    replay.step(frame(300, memory=70))
    assert episode.closed_at == at(300).timestamp()
    assert episode.recovery_reason == "valid_telemetry_recovered"


def test_critical_exact_boundaries(config):
    replay = Replay(config, scorer=broken)
    assert replay.step(frame(0, memory=94.99, disk=5))[1] == set()
    assert replay.step(frame(30, memory=95, disk=4.99))[1] == {"memory", "disk_capacity"}


def test_safety_pass_finishes_before_other_subsystem_scoring(config):
    def check(*_):
        assert replay.open["disk_capacity"].critical_at == at(0).timestamp()
        called.append(True)
        return {}

    called = []
    replay = Replay(config, scorer=check)
    replay.step(frame(0, memory=60, disk=4))
    assert called == [True] and replay.failures == 0


def test_three_fast_samples_cannot_replace_minimum_elapsed_time(config):
    replay = Replay(config, scorer=lambda *_: {"mad": 2.})
    for time in (0, 10, 20, 59):
        replay.step(frame(time, memory=80))
        assert not replay.episodes
    replay.step(frame(60, memory=80))
    assert replay.open["memory"].evidence_duration == 60


def test_phase3_past_only_mad_and_sustained_signal(config):
    feature_config = FeatureConfig()
    sequence = [(index * 30, {"memory_usage_pct": 62 + (index % 11) / 7})
                for index in range(135)]
    sequence += [(index * 30, {"memory_usage_pct": 65 + (index - 135) * .45})
                 for index in range(135, 166)]
    rows = list(iter_features(telemetry(sequence), feature_config))
    state = ShiftState()
    spec = config.subsystems["memory"]
    observed = [signals(row, spec, state, config, row.values["memory_usage_pct__current"],
                        row.sample_time.timestamp(), True) for row in rows]
    assert max(item.get("mad", 0) for item in observed[-20:]) > 1
    assert max(item.get("sustained", 0) for item in observed[-20:]) > 1
    low = spec.model_copy(update={"direction": "low"})
    assert signals(rows[-1], low, ShiftState(), config, rows[-1].values["memory_usage_pct__current"],
                   rows[-1].sample_time.timestamp(), True)["sustained"] == 0
    altered = sequence[:155] + [(index * 30, {"memory_usage_pct": 98.}) for index in range(155, 166)]
    prefix = list(iter_features(telemetry(altered), feature_config))[:155]
    assert [row.model_features() for row in prefix] == [row.model_features() for row in rows[:155]]


def test_ewma_and_cusum_on_persistent_shift_and_gap(config):
    spec = config.subsystems["memory"]
    state = ShiftState()
    for index in range(10):
        observed = state.update(50, index * 30, spec, config)
    assert observed == {"ewma": 0., "cusum": 0.}
    for index in range(10, 23):
        observed = state.update(66, index * 30, spec, config)
    assert observed["ewma"] > 1 and observed["cusum"] > 1
    assert state.update(90, 22 * 30, spec, config) == observed
    assert state.update(52, 900, spec, config) == {}
    assert state.samples == 1
