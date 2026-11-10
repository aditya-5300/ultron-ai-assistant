"""Scenario profiles and separate evaluation truth, never training features."""

from datetime import datetime

from pydantic import Field

from ultron_health_ml.contracts.telemetry import Contract
from ultron_health_ml.data_generation.config import ScenarioType

# All injection sizes and incident meanings live together, rather than in the CLI.
# Profile: metric end deltas, gradual vs bounded step, anomaly label, incident metric/direction.
PROFILES = {
    "NORMAL": ({}, False, False, None),
    "CPU_SPIKE": ({"cpu_usage_pct": 75}, False, True, None),
    "SUSTAINED_CPU_PRESSURE": ({"cpu_usage_pct": 65, "response_time_ms": 250}, False, True, ("cpu_usage_pct", "high")),
    "MEMORY_LEAK": ({"memory_usage_pct": 65}, True, True, ("memory_usage_pct", "high")),
    "DISK_EXHAUSTION": ({"disk_free_pct": -70}, True, True, ("disk_free_pct", "low")),
    "NETWORK_DEGRADATION": ({"network_latency_ms": 260, "response_time_ms": 350}, True, True, ("network_latency_ms", "high")),
    "RESPONSE_TIME_DEGRADATION": ({"response_time_ms": 1200}, True, True, ("response_time_ms", "high")),
    "ERROR_BURST": ({"error_rate": 0.3, "response_time_ms": 100}, False, True, ("error_rate", "high")),
    "THROUGHPUT_SURGE": ({"throughput_rps": 450, "cpu_usage_pct": 20, "response_time_ms": 50}, False, True, None),
    "MULTI_METRIC_DEGRADATION": ({"cpu_usage_pct": 65, "memory_usage_pct": 45, "network_latency_ms": 240, "response_time_ms": 1100, "error_rate": 0.25}, True, True, ("response_time_ms", "high")),
    "BACKUP_WORKLOAD": ({"cpu_usage_pct": 45, "disk_write_mbps": 160, "response_time_ms": 140, "memory_usage_pct": 6}, False, False, None),
    "COMPILATION_OR_BUILD_WORKLOAD": ({"cpu_usage_pct": 65, "memory_usage_pct": 22, "disk_write_mbps": 35, "response_time_ms": 180}, False, False, None),
}


class ScenarioTruth(Contract):
    scenario_id: str
    scenario_type: ScenarioType
    scenario_start: datetime
    scenario_end: datetime
    incident_time: datetime | None = None
    severity: str
    expected_behavior: str
    incident_definition: str
    intensity: float


class ObservationTruth(Contract):
    sample_id: str
    scenario: tuple[str, ...] = ()
    scenario_ids: tuple[str, ...] = ()
    is_anomaly: bool = False
    incident_ids: tuple[str, ...] = ()


class EvaluationTruth(Contract):
    evaluation_schema_version: str = "1.0.0"
    synthetic_only: bool = True
    scenarios: tuple[ScenarioTruth, ...]
    observations: tuple[ObservationTruth, ...]
    quality_events: tuple[dict, ...] = Field(default=())


def inject(values: dict[str, float], scenario: ScenarioTruth, sample_time: datetime):
    if not scenario.scenario_start <= sample_time < scenario.scenario_end:
        return
    deltas, gradual, *_ = PROFILES[scenario.scenario_type]
    progress = (sample_time - scenario.scenario_start).total_seconds() / (scenario.scenario_end - scenario.scenario_start).total_seconds()
    weight = progress if gradual else 1.0
    for name, delta in deltas.items():
        values[name] += delta * scenario.intensity * weight


def incident_description(name: str, thresholds) -> str:
    if name in {"NORMAL", "CPU_SPIKE", "THROUGHPUT_SURGE", "BACKUP_WORKLOAD", "COMPILATION_OR_BUILD_WORKLOAD"}:
        return "No incident by construction; unusual workload alone is not a failure."
    metric, direction = PROFILES[name][3]
    threshold = incident_threshold(metric, thresholds)
    persistence = f" continuously for {thresholds.sustained_seconds:g}s" if name == "SUSTAINED_CPU_PRESSURE" else ""
    return f"Latent simulated {metric} {'>=' if direction == 'high' else '<='} {threshold:g}{persistence}; not a real hardware-failure label."


def incident_threshold(metric: str, thresholds) -> float:
    return {
        "cpu_usage_pct": thresholds.cpu_pct,
        "memory_usage_pct": thresholds.memory_pct,
        "disk_free_pct": thresholds.disk_free_pct,
        "network_latency_ms": thresholds.network_latency_ms,
        "response_time_ms": thresholds.response_time_ms,
        "error_rate": thresholds.error_rate,
    }[metric]
