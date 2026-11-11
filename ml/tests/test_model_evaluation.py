from types import SimpleNamespace

import numpy as np
import pandas as pd

from ultron_health_ml.models.baselines import mad_baseline, threshold_baseline
from ultron_health_ml.models.config import TrainingConfig
from ultron_health_ml.data_generation.scenarios import EvaluationTruth, ObservationTruth, ScenarioTruth
from ultron_health_ml.models.evaluation import Truth, choose_threshold, episodes, evaluate, observability, row_metrics


def test_metrics_and_validation_threshold():
    actual = np.array([False, False, True, True])
    scores = np.array([0.15, 0.6, 0.8, 0.9])
    truth = Truth(actual, np.ones(4, dtype=bool), np.array([False, True, False, False]))
    choice = choose_threshold(scores, truth, TrainingConfig())
    assert choice["workload_fpr"] == 0
    assert choice["f1"] == 1
    metrics = row_metrics(actual, scores >= choice["threshold"], scores)
    assert metrics["recall"] == metrics["average_precision"] == metrics["roc_auc"] == 1


def test_episode_cooldown_gap_and_scope():
    times = pd.to_datetime([0, 30, 60, 90, 240, 270, 300], unit="s", utc=True)
    decisions = np.array([True, False, True, True, True, True, True])
    scope = np.array([True, True, True, True, True, False, True])
    actual = np.array([False, False, True, False, False, True, False])
    groups = episodes(times, decisions, scope, actual, TrainingConfig())
    assert [(item["flagged_rows"], item["duration_seconds"], item["true_alert"]) for item in groups] == [
        (3, 90, True), (1, 0, False), (1, 0, False)
    ]


def test_baselines_do_not_substitute_zero_for_missing():
    frame = pd.DataFrame({
        "cpu_usage_pct__current": [np.nan, 90, 65, 20],
        "memory_usage_pct__current": [np.nan, 60, 60, 60],
        "disk_free_pct__current": [np.nan, 40, 40, 4],
        "cpu_usage_pct__robust_z": [np.nan, 5, -5, 8],
    })
    run = SimpleNamespace(frame=frame, times=pd.to_datetime([0, 30, 60, 210], unit="s", utc=True))
    scores, flags = threshold_baseline(run, TrainingConfig())
    assert np.isnan(scores[0])
    assert flags.tolist() == [False, True, False, True]
    scores, flags = mad_baseline(run, TrainingConfig())
    assert np.isnan(scores[0])
    assert flags.tolist() == [False, False, True, False]
    coverage = observability(("cpu_usage_pct", "memory_usage_pct", "disk_free_pct"))
    assert coverage["CPU_SPIKE"] == "observable"
    assert coverage["SUSTAINED_CPU_PRESSURE"] == "partially_observable"
    assert coverage["NETWORK_DEGRADATION"] == "not_observable"


def test_scenario_delay_and_incident_eligibility():
    times = pd.to_datetime([0, 30, 60, 90, 120], unit="s", utc=True)
    run = SimpleNamespace(ids=("normal", "leak", "incident", "network", "backup"),
                          name="test-1", times=times)

    def scenario(name, start, end, incident=None):
        return ScenarioTruth(scenario_id=name, scenario_type=name, scenario_start=times[0] + pd.Timedelta(seconds=start),
                             scenario_end=times[0] + pd.Timedelta(seconds=end),
                             incident_time=times[0] + pd.Timedelta(seconds=incident) if incident else None,
                             severity="test", expected_behavior="test", incident_definition="test", intensity=1)

    labels = EvaluationTruth(
        scenarios=(scenario("MEMORY_LEAK", 25, 85, 60), scenario("NETWORK_DEGRADATION", 85, 105, 90),
                   scenario("BACKUP_WORKLOAD", 105, 145)),
        observations=tuple(ObservationTruth(sample_id=sample_id, scenario=(name,) if name else (), is_anomaly=anomaly)
                           for sample_id, name, anomaly in zip(run.ids, (
                               None, "MEMORY_LEAK", "MEMORY_LEAK", "NETWORK_DEGRADATION", "BACKUP_WORKLOAD"
                           ), (False, True, True, True, False))),
    )
    scores = np.array([0.1, 0.9, 0.2, 0.8, 0.7])
    summary, details, _ = evaluate([(run, scores, scores >= 0.5)], [labels],
                                   ("memory_usage_pct", "cpu_usage_pct"), TrainingConfig())
    assert summary["excluded_positive_rows"] == 1
    assert summary["incidents"] == dict(before=1, at=0, after=0, missed=0, unobservable_trigger=1,
                                        eligible=1, detection_rate=1)
    assert details[0]["delay_seconds"] == 5
    assert details[0]["relative_incident_seconds"] == -30
    assert details[1]["detected"] is None
    assert summary["workloads"]["BACKUP_WORKLOAD"]["fpr"] == 1
