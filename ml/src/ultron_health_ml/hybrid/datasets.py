"""Independent Phase 4.5 synthetic runs and hash-locked evaluation inputs."""

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from random import Random

from ultron_health_ml.contracts.telemetry import MetricReading, REAL_ULTRON_METRICS, TelemetryWindow
from ultron_health_ml.data_generation.config import GeneratorConfig, NoiseConfig, QualityEvent
from ultron_health_ml.data_generation.generator import GeneratedDataset, generate, write_dataset
from ultron_health_ml.features.validation import load_telemetry
from ultron_health_ml.models.artifact import write_json
from ultron_health_ml.models.datasets import sha256


PROTOCOL = "hybrid-fixtures-1"
KNOWN_PHASE4 = "8b9312bf1f98c8139aa9054fdea5d641398bb088"
FAMILIES = {
    "build": ("cpu", False, 0.060),
    "short_build": ("cpu", False, 0.006),
    "backup": ("disk_capacity", False, 0.044),
    "application_burst": ("cpu", False, 0.012),
    "legitimate_high_cpu": ("cpu", False, 0.056),
    "temporary_memory": ("memory", False, 0.041),
    "temporary_disk": ("disk_capacity", False, 0.041),
    "stable_high_memory": ("memory", False, 0.055),
    "sleep_gap": ("none", False, 0.025),
    "sensor_outage": ("none", False, 0.03),
    "cpu_contention": ("cpu", True, 0.048),
    "memory_creep": ("memory", True, 0.057),
    "memory_critical": ("memory", True, 0.058),
    "disk_decline": ("disk_capacity", True, 0.069),
    "disk_critical": ("disk_capacity", True, 0.068),
    "disk_heat": ("disk_health", True, 0.061),
    "battery_drain": ("battery_drain", True, 0.061),
    "process_stall": ("process", True, 0.045),
    "event_burst": ("events", True, 0.047),
}
SUBSYSTEM_METRIC = {
    "cpu": "cpu_usage_pct", "memory": "memory_usage_pct", "disk_capacity": "disk_free_pct",
    "disk_health": "disk_temperature_c", "battery_drain": "battery_pct",
    "process": "selected_process_nonresponding_count", "events": "relevant_event_count",
}
BENIGN = ("build", "short_build", "backup", "application_burst", "legitimate_high_cpu",
          "temporary_memory", "temporary_disk", "stable_high_memory", "sleep_gap", "sensor_outage")
FAULT_GROUPS = (
    ("cpu_contention", "memory_critical", "disk_decline", "event_burst"),
    ("memory_creep", "disk_critical", "disk_heat", "process_stall"),
    ("cpu_contention", "memory_critical", "disk_critical", "battery_drain"),
)


@dataclass(frozen=True)
class Event:
    event_id: str
    kind: str
    subsystem: str
    event_start: float
    event_end: float
    actionable_onset: float | None
    incident_time: float | None
    recovery_time: float
    severity: str
    observable_metrics: tuple[str, ...]
    intensity: float


def empty_directory(path):
    repo = Path(__file__).resolve().parents[4]
    path = path.resolve()
    protected = (repo / "ml/artifacts/isolation_forest", repo / "ml/artifacts/verified",
                 repo / "ml/generated/phase4-real-ultron", repo / "ml/generated/phase4-real-ultron-verified")
    if any(path == entry or entry in path.parents for entry in protected):
        raise ValueError("frozen Phase 4 directories are read-only")
    path.mkdir(parents=True, exist_ok=True)
    if any(path.iterdir()):
        raise ValueError(f"output directory must be empty: {path}")


def settings_digest(config):
    canonical = json.dumps(config.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _events(config, split, index, rng, start):
    duration = config.hours_per_run * 3600
    workloads = [BENIGN[(index * 2 + offset) % len(BENIGN)] for offset in range(5)]
    workloads = list(dict.fromkeys(["build", "backup", *workloads]))[:5]
    kinds = workloads if split == "train" else [*workloads, *FAULT_GROUPS[index % len(FAULT_GROUPS)]]
    if "battery_drain" in kinds:
        kinds.remove("battery_drain")
    rng.shuffle(kinds)
    if split != "train" and "battery_drain" in FAULT_GROUPS[index % len(FAULT_GROUPS)]:
        kinds.append("battery_drain")
    for position, kind in enumerate(kinds):
        subsystem, actionable, length = FAMILIES[kind]
        low = start.timestamp() + (0.06 + position * 0.100 + rng.uniform(0, 0.008)) * duration
        span = duration * length * rng.uniform(0.85, 1.13)
        high = low + span
        critical = kind in {"memory_critical", "disk_critical"}
        onset_fraction = (0.34 if kind in {"memory_creep", "memory_critical", "disk_decline", "disk_critical",
                                         "disk_heat", "battery_drain"} else 0.12)
        incidence = low + span * ((95 - 54) / (99 - 54) if kind == "memory_critical" else (46 - 5) / (46 - 2))
        metric = SUBSYSTEM_METRIC.get(subsystem)
        yield Event(f"{kind}-{position}", kind, subsystem, low, high,
                    low + span * onset_fraction if actionable else None,
                    incidence if critical else None, high + min(600., duration * .018),
                    "critical" if critical else "warning" if actionable else "benign",
                    (metric,) if metric else (), rng.uniform(.89, 1.12))


def _value(metric, value, event, time):
    fraction = (time - event.event_start) / (event.event_end - event.event_start)
    if not 0 <= fraction < 1:
        return value
    kind = event.kind
    intensity = event.intensity
    if kind == "disk_critical" and metric == "disk_free_pct":
        return 46 - 44 * fraction
    if kind == "memory_critical" and metric == "memory_usage_pct":
        return 54 + 45 * fraction
    ramps = {
        "memory_creep": ("memory_usage_pct", 39.),
        "disk_decline": ("disk_free_pct", -35.),
        "disk_heat": ("disk_temperature_c", 33.),
        "battery_drain": ("battery_pct", -30.),
    }
    if kind in ramps and metric == ramps[kind][0]:
        return value + ramps[kind][1] * intensity * fraction
    steps = {
        "build": {"cpu_usage_pct": 50, "memory_usage_pct": 14},
        "short_build": {"cpu_usage_pct": 63, "memory_usage_pct": 12},
        "backup": {"cpu_usage_pct": 28, "disk_free_pct": -4},
        "application_burst": {"cpu_usage_pct": 43, "memory_usage_pct": 9},
        "legitimate_high_cpu": {"cpu_usage_pct": 56},
        "temporary_memory": {"memory_usage_pct": 28},
        "temporary_disk": {"disk_free_pct": -10},
        "stable_high_memory": {"memory_usage_pct": 31},
        "cpu_contention": {"cpu_usage_pct": 61, "selected_process_nonresponding_count": 3},
        "process_stall": {"selected_process_nonresponding_count": 5},
        "event_burst": {"relevant_event_count": 9},
    }
    return value + steps.get(kind, {}).get(metric, 0) * intensity


def generate_run(settings, split, index, seed, directory):
    rng = Random(seed)
    start = datetime(2026, 2, 1, tzinfo=timezone.utc) + timedelta(hours=rng.randrange(24), days=index)
    events = list(_events(settings, split, index, rng, start))
    total = settings.hours_per_run * 3600
    quality = tuple(
        QualityEvent(metric=metric, kind="DEEP_COLLECTION_OUTAGE" if metric == "disk_free_pct" else "MISSING_SENSOR",
                     start_fraction=(event.event_start - start.timestamp()) / total,
                     duration_fraction=(event.event_end - event.event_start) / total)
        for event in events if event.kind == "sensor_outage"
        for metric in ("disk_free_pct", "memory_usage_pct")
    )
    config = GeneratorConfig(days=settings.hours_per_run / 24, random_seed=seed, start_time=start,
                             jitter_seconds=rng.uniform(1., 4.), skip_probability=rng.uniform(.002, .014),
                             overrun_probability=.004, overrun_seconds=90, missing_probability=rng.uniform(.0, .003),
                             noise=NoiseConfig(cpu=rng.uniform(1.4, 4.3), memory=rng.uniform(.06, .21)),
                             scenarios=(), quality_events=quality)
    base = generate(config)
    offsets = {"cpu_usage_pct": rng.uniform(-10, 15), "memory_usage_pct": rng.uniform(-9, 9),
               "disk_free_pct": rng.uniform(-25, 12), "disk_temperature_c": rng.uniform(-3, 6),
               "battery_capacity_pct": rng.uniform(-12, 8)}
    records = []
    previous = None
    for record in base.window.records:
        time = record.sample_time.timestamp()
        if any(event.event_start <= time < event.event_end for event in events if event.kind == "sleep_gap"):
            continue
        readings = {}
        for metric, reading in record.metrics.items():
            if metric not in REAL_ULTRON_METRICS:
                continue
            if reading.value is None:
                readings[metric] = reading
                continue
            observed = reading.observation_time.timestamp()
            value = reading.value + offsets.get(metric, 0.)
            for event in events:
                value = _value(metric, value, event, observed)
            minimum, maximum, *_ = REAL_ULTRON_METRICS[metric]
            value = max(minimum, min(value, maximum)) if maximum is not None else max(minimum, value)
            if metric.endswith("_count"):
                value = float(round(value))
            readings[metric] = MetricReading(value=value, observation_time=reading.observation_time,
                                             quality=reading.quality, availability=reading.availability)
        records.append(record.model_copy(update={
            "metrics": readings, "elapsed_seconds": None if previous is None else time - previous,
            "power_plan": ("balanced", "performance", "saver")[index % 3],
        }))
        previous = time
    window = TelemetryWindow(window_start=records[0].sample_time, window_end=records[-1].sample_time,
                             as_of=records[-1].sample_time, records=tuple(records))
    ids = {record.sample_id for record in records}
    base_truth = base.evaluation.model_copy(update={"observations": tuple(
        item for item in base.evaluation.observations if item.sample_id in ids
    )})
    write_dataset(GeneratedDataset(window, base_truth, config), directory)
    write_json(directory / "events.json", [asdict(event) for event in events])


def prepare(root, config):
    empty_directory(root)
    records = []
    for split, seeds in (("train", config.train_seeds), ("validation", config.validation_seeds),
                         ("holdout", config.holdout_seeds)):
        for index, seed in enumerate(seeds):
            name = f"{split}-{seed}"
            directory = root / name
            generate_run(config, split, index, seed, directory)
            records.append(dict(run=name, split=split, seed=seed,
                                sha256={name: sha256(directory / name) for name in
                                        ("manifest.json", "telemetry.csv", "scenarios.json", "events.json")}))
    manifest = dict(protocol=PROTOCOL, settings_sha256=settings_digest(config), runs=records)
    write_json(root / "locked_inputs.json", manifest)
    return manifest


def verify_lock(root, config, expected_sha256):
    path = root / "locked_inputs.json"
    if sha256(path) != expected_sha256:
        raise ValueError("locked input manifest checksum changed")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("protocol") != PROTOCOL or manifest.get("settings_sha256") != settings_digest(config):
        raise ValueError("locked input protocol or configuration changed")
    expected = [(f"{split}-{seed}", split, seed) for split, seeds in (
        ("train", config.train_seeds), ("validation", config.validation_seeds), ("holdout", config.holdout_seeds)
    ) for seed in seeds]
    actual = [(run["run"], run["split"], run["seed"]) for run in manifest["runs"]]
    if expected != actual:
        raise ValueError("locked split membership changed")
    hashes = [run["sha256"]["telemetry.csv"] for run in manifest["runs"]]
    if len(set(hashes)) != len(hashes):
        raise ValueError("telemetry repeated across independent runs")
    return manifest


def open_telemetry(root, run):
    directory = root / run["run"]
    for name in ("manifest.json", "telemetry.csv"):
        if sha256(directory / name) != run["sha256"][name]:
            raise ValueError(f"locked input checksum changed: {run['run']}/{name}")
    return load_telemetry(directory)


def open_events(root, run):
    directory = root / run["run"]
    for name in ("events.json", "scenarios.json"):
        if sha256(directory / name) != run["sha256"][name]:
            raise ValueError(f"locked label checksum changed: {run['run']}/{name}")
    return [Event(**row) for row in json.loads((directory / "events.json").read_text(encoding="utf-8"))]
