"""Focused independent synthetic traces; prior phase inputs are read-only."""

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from random import Random

from ultron_health_ml.contracts.telemetry import REAL_ULTRON_METRICS, MetricReading, TelemetryWindow
from ultron_health_ml.data_generation.config import GeneratorConfig, NoiseConfig, QualityEvent
from ultron_health_ml.data_generation.generator import GeneratedDataset, generate, write_dataset
from ultron_health_ml.hybrid.datasets import Event, PROTOCOL as INPUT_SCHEMA, _value, settings_digest, verify_lock
from ultron_health_ml.models.artifact import write_json
from ultron_health_ml.models.datasets import sha256
from ultron_health_ml.nuisance.datasets import new_output as b_output, variation
from ultron_health_ml.nuisance.replay import read_references
from ultron_health_ml.reduced.protocol import POINTS, PROTOCOL, RULES, SCOPE

BENIGN = ("build", "backup", "short_build", "legitimate_high_cpu", "application_burst",
          "temporary_disk", "temporary_memory", "sleep_gap", "sensor_outage", "stable_high_memory")
FAULTS = ("cpu_contention", "cpu_oscillating", "disk_decline", "disk_critical",
          "process_stall", "process_repeated", "memory_critical")
MAPPING = {
    "cpu_contention": ("cpu", "cpu_usage_pct"),
    "cpu_oscillating": ("cpu", "cpu_usage_pct"),
    "disk_decline": ("disk_capacity", "disk_free_pct"),
    "disk_critical": ("disk_capacity", "disk_free_pct"),
    "process_stall": ("process", "selected_process_nonresponding_count"),
    "process_repeated": ("process", "selected_process_nonresponding_count"),
    "memory_critical": ("memory", "memory_usage_pct"),
}


def new_output(path):
    candidate = path.resolve()
    repo = Path(__file__).resolve().parents[4]
    for frozen in (repo / "ml/generated/phase45b", repo / "ml/artifacts/phase45b"):
        if candidate == frozen or frozen in candidate.parents:
            raise ValueError("Phase 4.5-B outputs are read-only")
    b_output(candidate)


def protocol():
    module = Path(__file__).parent
    package = module.parent
    sources = sorted(module.glob("*.py"))
    for name in ("features", "hybrid", "nuisance", "data_generation"):
        sources.extend(sorted((package / name).glob("*.py")))
    return dict(protocol=PROTOCOL, scope=SCOPE, rules=RULES, points=[asdict(point) for point in POINTS],
                scenarios=dict(benign=BENIGN, faults=FAULTS),
                source_sha256={path.relative_to(package).as_posix(): sha256(path) for path in sources})


def references(repo, config_directory):
    result = read_references(repo, config_directory)
    path = config_directory / "phase45b_reference_sha256.json"
    expected = json.loads(path.read_text(encoding="utf-8"))
    if repo is not None:
        for name, digest in expected.items():
            if sha256(repo / name) != digest:
                raise ValueError(f"frozen Phase 4.5-B reference changed: {name}")
    result["phase45b"] = dict(files_verified=len(expected) if repo is not None else 0,
                              manifest_sha256=sha256(path), hashes=expected)
    return result


def scenarios(start, hours, index, rng):
    choices = ["build", "backup", "short_build", *[BENIGN[3 + (index + offset) % 7] for offset in range(4)], *FAULTS]
    rng.shuffle(choices)
    duration = hours * 3600
    for position, kind in enumerate(choices):
        low = start.timestamp() + duration * (.025 + .066 * position + rng.uniform(0, .004))
        length = .007 if kind == "short_build" else .025 if kind in {"application_burst", "sleep_gap"} else .05
        span = duration * length * rng.uniform(.85, 1.12)
        subsystem, metric = MAPPING.get(kind, ("none", None))
        critical = kind in {"disk_critical", "memory_critical"}
        incidence = (95 - 54) / 45 if kind == "memory_critical" else (46 - 5) / 44
        onset_fraction = .3 if kind in {"disk_critical", "disk_decline", "memory_critical"} else .12
        yield Event(f"{kind}-{position}", kind, subsystem, low, low + span,
                    low + span * onset_fraction if metric else None,
                    low + span * incidence if critical else None, low + span + 600.,
                    "critical" if critical else "warning" if metric else "benign",
                    (metric,) if metric else (), rng.uniform(.84, 1.16))


def overlay(metric, value, event, observed, variant):
    elapsed = observed - event.event_start
    span = event.event_end - event.event_start
    if not 0 <= elapsed < span:
        return value
    if event.kind == "cpu_oscillating":
        if metric == "cpu_usage_pct":
            return value + (62 if int(elapsed / 105) % 5 != 4 else 37) * event.intensity
        if metric == "selected_process_nonresponding_count":
            return value + 3
    if event.kind == "process_repeated" and metric == "selected_process_nonresponding_count":
        return value + (6 if int(elapsed / 150) % 7 != 6 else 2)
    if event.kind == "disk_decline" and metric == "disk_free_pct":
        return value - (30 + 3 * variant) * elapsed / span
    return _value(metric, value, event, observed) + variation(metric, observed, asdict(event), variant)


def generate_run(config, index, seed, path):
    rng = Random(seed)
    start = datetime(2026, 3, 1, tzinfo=timezone.utc) + timedelta(days=index, hours=rng.randrange(24))
    events = tuple(scenarios(start, config.hours_per_run, index, rng))
    length = config.hours_per_run * 3600
    quality = tuple(QualityEvent(metric="disk_free_pct", kind="DEEP_COLLECTION_OUTAGE",
                                start_fraction=(event.event_start - start.timestamp()) / length,
                                duration_fraction=(event.event_end - event.event_start) / length)
                    for event in events if event.kind == "sensor_outage")
    settings = GeneratorConfig(days=config.hours_per_run / 24, random_seed=seed, start_time=start, scenarios=(),
                               quality_events=quality, deep_interval_seconds=rng.choice((225, 300, 420)),
                               jitter_seconds=rng.uniform(1, 4), skip_probability=rng.uniform(.003, .015),
                               overrun_probability=.003, overrun_seconds=90,
                               missing_probability=rng.uniform(.001, .004),
                               noise=NoiseConfig(cpu=rng.uniform(1.5, 5.), memory=rng.uniform(.07, .17)))
    base = generate(settings)
    offsets = dict(cpu_usage_pct=rng.uniform(-12, 18), disk_free_pct=rng.uniform(-25, 6),
                   memory_usage_pct=rng.uniform(-9, 10))
    variant = rng.randrange(1, 6)
    records = []
    sleep = tuple(event for event in events if event.kind == "sleep_gap")
    previous = None
    ids = set()
    for item in base.window.records:
        now = item.sample_time.timestamp()
        if any(event.event_start <= now < event.event_end for event in sleep):
            continue
        readings = {}
        for metric in REAL_ULTRON_METRICS:
            reading = item.metrics[metric]
            if reading.value is None:
                readings[metric] = reading
                continue
            observed = reading.observation_time.timestamp()
            value = reading.value + offsets.get(metric, 0.)
            for event in events:
                value = overlay(metric, value, event, observed, variant)
            lower, upper, *_ = REAL_ULTRON_METRICS[metric]
            value = max(lower, min(value, upper)) if upper is not None else max(lower, value)
            if metric.endswith("_count"):
                value = float(round(value))
            readings[metric] = MetricReading(value=value, observation_time=reading.observation_time,
                                            quality=reading.quality, availability=reading.availability)
        records.append(item.model_copy(update={"metrics": readings,
                       "elapsed_seconds": now - previous if previous is not None else None,
                       "power_plan": ("balanced", "saver", "performance")[index % 3]}))
        previous = now
        ids.add(item.sample_id)
    window = TelemetryWindow(window_start=records[0].sample_time, window_end=records[-1].sample_time,
                             as_of=records[-1].sample_time, records=tuple(records))
    write_dataset(GeneratedDataset(window, base.evaluation.model_copy(update={"observations": tuple(
        item for item in base.evaluation.observations if item.sample_id in ids)}), settings), path)
    write_json(path / "events.json", [asdict(event) for event in events])


def prepare(path, config):
    new_output(path)
    lock = dict(protocol=INPUT_SCHEMA, experiment=protocol(), settings_sha256=settings_digest(config), runs=[])
    for split, seeds in (("train", config.train_seeds), ("validation", config.validation_seeds),
                         ("holdout", config.holdout_seeds)):
        for index, seed in enumerate(seeds):
            name = f"{split}-{seed}"
            generate_run(config, index, seed, path / name)
            lock["runs"].append(dict(run=name, seed=seed, split=split,
                sha256={file: sha256(path / name / file)
                        for file in ("manifest.json", "telemetry.csv", "scenarios.json", "events.json")}))
    write_json(path / "locked_inputs.json", lock)
    return sha256(path / "locked_inputs.json")


def open_lock(path, config, digest):
    lock = verify_lock(path, config, digest)
    if lock.get("experiment") != json.loads(json.dumps(protocol())):
        raise ValueError("C evaluation contract differs from the locked version")
    return lock
