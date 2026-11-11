import numpy as np
import pytest

from ultron_health_ml.data_generation.config import GeneratorConfig, SCENARIO_TYPES, ScenarioConfig
from ultron_health_ml.data_generation.generator import generate, telemetry_frame
from ultron_health_ml.data_generation.scenarios import PROFILES, inject


def dataset(kind, **overrides):
    scenario = ScenarioConfig(scenario_type=kind, start_fraction=0.2, duration_fraction=0.5)
    return generate(GeneratorConfig(**(dict(days=0.1, random_seed=42, missing_probability=0,
                                            scenarios=(scenario,)) | overrides)))


@pytest.mark.parametrize("kind", SCENARIO_TYPES)
def test_all_scenarios_injected_and_bounded(kind):
    result = dataset(kind)
    truth = result.evaluation.scenarios[0]
    assert truth.scenario_type == kind
    assert truth.expected_behavior and truth.incident_definition
    assert truth.scenario_start < truth.scenario_end
    for record, label in zip(result.window.records, result.evaluation.observations):
        active = truth.scenario_start <= record.sample_time < truth.scenario_end
        assert bool(label.scenario) == active
        assert label.is_anomaly == (active and PROFILES[kind][2])
    original = {"cpu_usage_pct": 20.0}
    inject(original, truth, truth.scenario_end)
    assert original == {"cpu_usage_pct": 20.0}


@pytest.mark.parametrize("kind,metric,direction", [
    ("MEMORY_LEAK", "memory_usage_pct", 1), ("DISK_EXHAUSTION", "disk_free_pct", -1),
    ("NETWORK_DEGRADATION", "network_latency_ms", 1),
    ("RESPONSE_TIME_DEGRADATION", "response_time_ms", 1),
])
def test_gradual_tendencies(kind, metric, direction):
    result = dataset(kind)
    truth = result.evaluation.scenarios[0]
    active = [record.metrics[metric].value for record in result.window.records
              if truth.scenario_start <= record.sample_time < truth.scenario_end]
    slope = np.polyfit(np.arange(len(active)), active, 1)[0]
    assert slope * direction > 0
    assert np.mean(active[-10:]) * direction > np.mean(active[:10]) * direction + 10
    assert truth.incident_time is not None
    assert truth.scenario_start <= truth.incident_time < truth.scenario_end


@pytest.mark.parametrize("kind", ["CPU_SPIKE", "BACKUP_WORKLOAD", "COMPILATION_OR_BUILD_WORKLOAD", "THROUGHPUT_SURGE"])
def test_temporary_workload_recovery_and_no_incident(kind):
    result = dataset(kind)
    baseline = generate(GeneratorConfig(days=0.1, scenarios=(), missing_probability=0))
    truth = result.evaluation.scenarios[0]
    assert truth.incident_time is None
    recovered = [index for index, record in enumerate(result.window.records) if record.sample_time >= truth.scenario_end]
    for index in recovered:
        assert result.window.records[index].metrics == baseline.window.records[index].metrics
    frame = telemetry_frame(result)
    assert frame.cpu_usage_pct.max() > 50
    if kind in {"BACKUP_WORKLOAD", "COMPILATION_OR_BUILD_WORKLOAD"}:
        assert not any(label.is_anomaly for label in result.evaluation.observations)


def test_multi_metric_correlated_degradation():
    result = dataset("MULTI_METRIC_DEGRADATION")
    truth = result.evaluation.scenarios[0]
    frame = telemetry_frame(result)
    active = [truth.scenario_start <= record.sample_time < truth.scenario_end for record in result.window.records]
    subset = frame.loc[active, ["cpu_usage_pct", "memory_usage_pct", "network_latency_ms", "response_time_ms", "error_rate"]]
    assert subset.corr().min().min() > 0.85
    assert truth.incident_time is not None


def test_sustained_pressure_incident_requires_duration():
    result = dataset("SUSTAINED_CPU_PRESSURE")
    truth = result.evaluation.scenarios[0]
    assert truth.incident_time is not None
    assert (truth.incident_time - truth.scenario_start).total_seconds() >= result.config.incidents.sustained_seconds


def test_error_burst_bounded_and_thresholded():
    result = dataset("ERROR_BURST")
    truth = result.evaluation.scenarios[0]
    assert truth.incident_time is not None
    for record in result.window.records:
        if record.sample_time >= truth.scenario_end:
            assert record.metrics["error_rate"].value < 0.01


def test_incident_optional_if_threshold_not_reached():
    setting = ScenarioConfig(scenario_type="MEMORY_LEAK", start_fraction=0.2, duration_fraction=0.5, intensity=0.05)
    result = generate(GeneratorConfig(days=0.1, scenarios=(setting,), missing_probability=0))
    assert result.evaluation.scenarios[0].incident_time is None
    assert any(label.is_anomaly for label in result.evaluation.observations)


def test_disabled_and_overlapping_scenarios():
    settings = tuple(ScenarioConfig(scenario_type=name, start_fraction=0.2, duration_fraction=0.5, enabled=name != "NORMAL")
                     for name in ("NORMAL", "CPU_SPIKE", "ERROR_BURST"))
    result = generate(GeneratorConfig(days=0.02, scenarios=settings))
    assert len(result.evaluation.scenarios) == 2
    assert any(len(label.scenario_ids) == 2 for label in result.evaluation.observations)
