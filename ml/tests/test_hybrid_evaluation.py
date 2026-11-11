from pathlib import Path

from ultron_health_ml.hybrid.config import load_settings
from ultron_health_ml.hybrid.datasets import Event
from ultron_health_ml.hybrid.detector import Episode
from ultron_health_ml.hybrid.evaluation import Trace, choose_candidate, compare, evaluate


def config():
    return load_settings(Path(__file__).resolve().parents[1] / "configs/hybrid_detection.yaml")


def event(key, kind, subsystem, start, end, onset=None, incident=None):
    return Event(key, kind, subsystem, start, end, onset, incident, end + 10,
                 "critical" if incident else "warning" if onset else "benign", (), 1.)


def test_same_subsystem_alerts_episode_matching_flapping_and_coverage():
    events = [event("build", "build", "cpu", 0, 60),
              event("disk", "disk_critical", "disk_capacity", 75, 160, 100, 125)]
    episodes = [Episode("unrelated", "cpu", "pressure", 10, 40, "warning", closed_at=50),
                Episode("wrong-key", "cpu", "pressure", 110, 130, "warning", closed_at=155),
                Episode("disk", "disk_capacity", "pressure", 120, 145, "critical", closed_at=175,
                        critical_at=130)]
    trace = Trace("run", [0, 110, 130, 180], [set(), {"cpu"}, {"disk_capacity"}, set()],
                  [{"cpu": True}, {"cpu": False}, {"cpu": True}, {"cpu": True}], {"disk_capacity": [130]},
                  episodes, 86400, 1, 0, 0)
    result, events, episode_rows, per_subsystem, exposure = evaluate([trace], {"run": events}, config())
    assert result["event_recall"] == 1 and events[0]["delay_seconds"] == 20
    assert result["critical_expected"] == 1 and result["critical_missed"] == result["critical_delayed"] == 0
    assert events[0]["recovery_delay_seconds"] == 15
    assert result["false_episodes_per_day"] == 2 and result["workload_nuisance_rate"] == 1
    assert result["flap_count"] == 1 and episode_rows[1]["flap"]
    assert per_subsystem["cpu"]["insufficient_telemetry_rate"] == .25
    assert exposure[0]["nuisance"]


def test_alert_budget_selection_and_comparison():
    def result(false, recall, delay):
        return dict(false_episodes_per_day=false, workload_nuisance_rate=0,
                    event_recall=recall, median_delay_seconds=delay)
    cfg = config()
    assert choose_candidate([(1., result(2, 1, 100)), (2., result(0, .6, 150))], cfg) == 2.
    assert compare(result(0, .7, 70), result(.3, .7, 100), cfg)["meets_incremental_gate"]
    assert not compare(result(0, .9, 70), result(3, .5, 100), cfg)["comparable"]
    assert not compare(result(0, .65, 70), result(.3, .7, 100), cfg)["meets_incremental_gate"]
