"""Offline CLI and deterministic generator. Never opens ULTRON's SQLite files."""

import argparse
from dataclasses import dataclass
from datetime import timedelta
import hashlib
import json
import math
import platform
from importlib.metadata import version
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import numpy as np
import pandas as pd
import yaml

from ultron_health_ml import __version__
from ultron_health_ml.contracts.telemetry import (
    DEEP_METRICS, METRIC_SPECS, TELEMETRY_SCHEMA_VERSION,
    MetricReading, TelemetryRecord, TelemetryWindow,
)
from ultron_health_ml.data_generation.config import GeneratorConfig, load_config
from ultron_health_ml.data_generation.scenarios import (
    PROFILES, EvaluationTruth, ObservationTruth, ScenarioTruth,
    incident_description, incident_threshold, inject,
)


@dataclass(frozen=True)
class GeneratedDataset:
    window: TelemetryWindow
    evaluation: EvaluationTruth
    config: GeneratorConfig


def _scenarios(config):
    seconds = config.days * 86400
    scenarios = []
    for index, setting in enumerate(config.scenarios):
        if not setting.enabled:
            continue
        kind = setting.scenario_type
        anomaly = PROFILES[kind][2]
        scenarios.append(ScenarioTruth(
            scenario_id=str(uuid5(NAMESPACE_URL, f"ultron-synthetic:{config.random_seed}:{index}:{kind}")),
            scenario_type=kind,
            scenario_start=config.start_time + timedelta(seconds=seconds * setting.start_fraction),
            scenario_end=config.start_time + timedelta(seconds=seconds * (setting.start_fraction + setting.duration_fraction)),
            severity="warning" if anomaly else "info",
            expected_behavior=(
                "Bounded simulated degradation; incident only if its threshold is crossed. "
                "At window end the synthetic workload/process is removed or disk space cleaned up."
                if anomaly else "Legitimate/normal workload; no incident label, with recovery after the window."
            ),
            incident_definition=incident_description(kind, config.incidents),
            intensity=setting.intensity,
        ))
    return scenarios


class _NormalSystem:
    def __init__(self, rng, noise):
        self.rng, self.noise = rng, noise
        self.cpu_noise = self.net_noise = 0.0
        self.memory = 48.0
        self.battery = 82.0

    def step(self, timestamp, elapsed, seconds):
        # Correlated noise retains recent history; memory responds more slowly than CPU.
        decay = math.exp(-elapsed / 120)
        innovation = math.sqrt(1 - decay**2)
        self.cpu_noise = decay * self.cpu_noise + innovation * self.rng.normal(0, self.noise.cpu)
        self.net_noise = decay * self.net_noise + innovation * self.rng.normal(0, self.noise.network)
        day_seconds = timestamp.hour * 3600 + timestamp.minute * 60 + timestamp.second
        workload = 0.4 + 0.18 * math.sin(2 * math.pi * day_seconds / 86400 - 1.5)
        workload += 0.10 * math.sin(2 * math.pi * seconds / 5400)
        cpu = 14 + 54 * workload + self.cpu_noise
        self.memory += (44 + 14 * workload - self.memory) * (1 - math.exp(-elapsed / 900))
        self.memory += self.rng.normal(0, self.noise.memory) * math.sqrt(elapsed / 30)
        network = 20 + 5 * workload + self.net_noise
        ac_online = (seconds / 3600) % 12 < 8
        self.battery = float(np.clip(self.battery + (10 if ac_online else -(5 + cpu / 10)) * elapsed / 3600, 0, 100))
        return {
            "cpu_usage_pct": cpu,
            "memory_usage_pct": self.memory,
            "disk_free_pct": 67 - 0.2 * seconds / 86400,
            "battery_pct": self.battery,
            "battery_capacity_pct": 91.0,
            "disk_temperature_c": 34 + 0.14 * cpu,
            "selected_process_nonresponding_count": 0.0,
            "relevant_event_count": 0.0,
            "network_latency_ms": network,
            "response_time_ms": 80 + 1.8 * cpu + 0.4 * network + self.cpu_noise,
            "error_rate": 0.001 + abs(self.net_noise) * 0.0001,
            "throughput_rps": 30 + 150 * workload + cpu * 0.5,
            "disk_write_mbps": 1 + 4 * workload,
        }, ac_online


def _quality_active(config, name, kind, seconds):
    total = config.days * 86400
    return any(event.metric == name and event.kind == kind
               and total * event.start_fraction <= seconds < total * (event.start_fraction + event.duration_fraction)
               for event in config.quality_events)


def generate(config: GeneratorConfig) -> GeneratedDataset:
    # Independent streams ensure sensor loss never changes the latent system physics.
    streams = np.random.SeedSequence(config.random_seed).spawn(3)
    clock_rng, value_rng, quality_rng = (np.random.default_rng(stream) for stream in streams)
    system = _NormalSystem(value_rng, config.noise)
    scenarios = _scenarios(config)
    incident_times = {}
    sustained_since = {}
    deep_cache = {}
    deep_attempt = -math.inf
    records, observations = [], []
    seconds, prior_seconds, tick = 0.0, 0.0, 0
    collection_overrun = 0.0
    duration = config.days * 86400
    while seconds < duration:
        sample_time = config.start_time + timedelta(seconds=seconds)
        elapsed = seconds - prior_seconds if tick else config.interval_seconds
        values, ac_online = system.step(sample_time, elapsed, seconds)
        active = [scenario for scenario in scenarios if scenario.scenario_start <= sample_time < scenario.scenario_end]
        for scenario in active:
            inject(values, scenario, sample_time)
        for name, (lower, upper, *_) in METRIC_SPECS.items():
            values[name] = float(np.clip(values[name], lower, upper if upper is not None else np.inf))

        # Truth follows latent values, even if the sensor or observation is subsequently lost.
        for scenario in active:
            definition = PROFILES[scenario.scenario_type][3]
            if definition is None:
                continue
            metric, direction = definition
            threshold = incident_threshold(metric, config.incidents)
            crossed = values[metric] >= threshold if direction == "high" else values[metric] <= threshold
            if crossed:
                sustained_since.setdefault(scenario.scenario_id, sample_time)
                held = (sample_time - sustained_since[scenario.scenario_id]).total_seconds()
                if scenario.scenario_type != "SUSTAINED_CPU_PRESSURE" or held >= config.incidents.sustained_seconds:
                    incident_times.setdefault(scenario.scenario_id, sample_time)
            else:
                sustained_since.pop(scenario.scenario_id, None)

        skipped = tick > 0 and clock_rng.random() < config.skip_probability
        if not skipped:
            refresh = seconds - deep_attempt >= config.deep_interval_seconds
            if refresh:
                deep_attempt = seconds
            readings = {}
            for name, value in values.items():
                missing = quality_rng.random() < config.missing_probability or _quality_active(config, name, "MISSING_SENSOR", seconds)
                if name in DEEP_METRICS:
                    outage = _quality_active(config, name, "DEEP_COLLECTION_OUTAGE", seconds)
                    if refresh and not missing and not outage:
                        deep_cache[name] = (value, sample_time)
                    if name in deep_cache and not missing:
                        value, observed_at = deep_cache[name]
                        age = (sample_time - observed_at).total_seconds()
                        quality = "stale" if age >= config.deep_stale_after_seconds else "cached" if age else "fresh"
                    else:
                        missing = True
                else:
                    observed_at, quality = sample_time, "fresh"
                readings[name] = MetricReading(
                    value=None if missing else value,
                    observation_time=None if missing else observed_at,
                    availability="unavailable" if missing else "available",
                    quality="missing" if missing else quality,
                )
            sample_id = f"synthetic-{config.random_seed}-{tick:08d}"
            records.append(TelemetryRecord(
                source="synthetic", source_version=__version__, sample_id=sample_id,
                sample_time=sample_time, nominal_interval_seconds=config.interval_seconds,
                elapsed_seconds=(sample_time - records[-1].sample_time).total_seconds() if records else None,
                collection_duration_seconds=collection_overrun,
                ac_online=ac_online, power_plan="balanced", metrics=readings,
            ))
            observations.append(ObservationTruth(
                sample_id=sample_id,
                scenario=tuple(scenario.scenario_type for scenario in active),
                scenario_ids=tuple(scenario.scenario_id for scenario in active),
                is_anomaly=any(PROFILES[scenario.scenario_type][2] for scenario in active),
                incident_ids=tuple(scenario.scenario_id for scenario in active if scenario.scenario_id in incident_times),
            ))
        prior_seconds = seconds
        spacing = config.interval_seconds + clock_rng.uniform(-config.jitter_seconds, config.jitter_seconds)
        collection_overrun = config.overrun_seconds if clock_rng.random() < config.overrun_probability else 0.0
        spacing += collection_overrun
        seconds = round(seconds + spacing, 6)
        tick += 1
    finalized = tuple(scenario.model_copy(update={"incident_time": incident_times.get(scenario.scenario_id)}) for scenario in scenarios)
    return GeneratedDataset(
        window=TelemetryWindow(window_start=config.start_time, window_end=records[-1].sample_time,
                               as_of=records[-1].sample_time, records=tuple(records)),
        evaluation=EvaluationTruth(scenarios=finalized, observations=tuple(observations),
                                   quality_events=tuple(event.model_dump(mode="json") for event in config.quality_events)),
        config=config,
    )


def telemetry_frame(dataset: GeneratedDataset) -> pd.DataFrame:
    rows = []
    for record in dataset.window.records:
        row = record.model_dump(mode="json", exclude={"metrics"})
        for name, reading in record.metrics.items():
            row[name] = reading.value
            row[f"{name}__observation_time"] = reading.observation_time.isoformat() if reading.observation_time else None
            row[f"{name}__availability"] = reading.availability
            row[f"{name}__quality"] = reading.quality
        rows.append(row)
    return pd.DataFrame(rows)


def write_dataset(dataset: GeneratedDataset, output: Path):
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError("output directory must be empty; existing files will not be overwritten")
    frame = telemetry_frame(dataset)
    frame.to_csv(output / "telemetry.csv", index=False, na_rep="", float_format="%.10g")
    (output / "scenarios.json").write_text(dataset.evaluation.model_dump_json(indent=2) + "\n", encoding="utf-8")
    # The manifest reproduces settings and binds truth/CSV files without importing labels.
    manifest = {
        "generator_version": __version__, "telemetry_schema_version": TELEMETRY_SCHEMA_VERSION,
        "config": dataset.config.model_dump(mode="json"), "record_count": len(frame),
        "window_start": dataset.window.window_start.isoformat(),
        "window_end": dataset.window.window_end.isoformat(), "as_of": dataset.window.as_of.isoformat(),
        "versions": {"python": platform.python_version(), "numpy": np.__version__, "pandas": pd.__version__,
                     "pydantic": version("pydantic"), "PyYAML": version("PyYAML")},
        "sha256": {name: hashlib.sha256((output / name).read_bytes()).hexdigest() for name in ("telemetry.csv", "scenarios.json")},
        "warning": "Synthetic telemetry is not evidence that ULTRON predicts real hardware failures.",
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--days", type=float)
    parser.add_argument("--interval", type=int, choices=(15, 30, 60, 120, 300))
    parser.add_argument("--seed", type=int)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        config = load_config(args.config, days=args.days, interval_seconds=args.interval, random_seed=args.seed)
        dataset = generate(config)
        write_dataset(dataset, args.output)
    except (ValueError, OSError, yaml.YAMLError) as error:
        parser.exit(2, f"Generator error: {error}\n")
    print(f"Wrote {len(dataset.window.records)} synthetic observations and {len(dataset.evaluation.scenarios)} scenarios to {args.output}")


if __name__ == "__main__":
    main()
