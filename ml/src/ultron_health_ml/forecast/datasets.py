"""Independent synthetic runs with separately sealed future-event sidecars."""

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
from math import sin
from pathlib import Path
from random import Random

from ultron_health_ml import __version__
from ultron_health_ml.contracts.telemetry import MetricReading, TelemetryRecord, TelemetryWindow
from ultron_health_ml.data_generation.config import GeneratorConfig
from ultron_health_ml.data_generation.generator import GeneratedDataset, write_dataset
from ultron_health_ml.data_generation.scenarios import EvaluationTruth
from ultron_health_ml.features.validation import load_telemetry
from ultron_health_ml.models.artifact import write_json
from ultron_health_ml.models.datasets import sha256
from ultron_health_ml.reduced.datasets import new_output as previous_guard, references as prior_references
from ultron_health_ml.forecast.contract import Actionable, Event, METRICS, SYSTEMS

PROTOCOL = "phase5-offline-v1"


def output_directory(path):
    resolved = path.resolve()
    repo = Path(__file__).resolve().parents[4]
    for frozen in (repo / "ml/generated/phase45c", repo / "ml/artifacts/phase45c"):
        if resolved == frozen or frozen in resolved.parents:
            raise ValueError("Phase 4.5-C outputs are read-only")
    previous_guard(resolved)


def references(repo, configs):
    result = prior_references(repo, configs)
    manifest = configs.parent.parent / "docs/phase45c-reference/reference_sha256.json"
    hashes = json.loads(manifest.read_text(encoding="utf8"))
    if repo is not None:
        for path, digest in hashes.items():
            if sha256(repo / path) != digest:
                raise ValueError(f"frozen C reference changed: {path}")
    result["phase45c"] = dict(files_verified=len(hashes) if repo is not None else 0,
                              manifest_sha256=sha256(manifest), hashes=hashes)
    return result


def experiment_definition(config, targets):
    package = Path(__file__).resolve().parent.parent
    files = [path for name in ("forecast", "features", "data_generation", "contracts")
             for path in sorted((package / name).glob("*.py"))]
    files += [package / "hybrid/detector.py"]
    return dict(protocol=PROTOCOL, settings=config.model_dump(mode="json"), targets=targets.model_dump(mode="json"),
                source_sha256={path.relative_to(package).as_posix(): sha256(path) for path in files})


class Device:
    def __init__(self, seed, hours):
        rng = Random(seed)
        self.span = hours * 3600
        self.offset = rng.uniform(-7., 9.)
        self.disk_base = rng.uniform(36., 60.)
        self.variant = seed % 5
        self.shift = rng.uniform(0., .025)
        definitions = [
            ("short_build", .035, .005), ("build", .13, .042), ("backup", .27, .036),
            ("interactive", .32, .024), ("legitimate_cpu", .41, .038), ("build", .64, .052),
            ("launch_stall", .72, .014), ("backup", .80, .045),
            ("cpu_ramp", .19, .058), ("cpu_oscillation", .58, .060),
            ("process_ramp", .35, .055), ("process_step", .86, .064),
            ("disk_linear", .05, .185), ("disk_stable_near", .29, .12),
            ("disk_accelerating", .43, .177), ("disk_temporary", .71, .10),
        ]
        self.intervals = tuple((name, (start + self.shift) * self.span, duration * self.span)
                               for name, start, duration in definitions)
        self.sleep = (.753 * self.span, .763 * self.span)
        self.disk_gap = (.5 * self.span, (.513 + .003 * self.variant) * self.span)

    def values(self, second):
        cpu = 35 + self.offset + 7 * sin(second / 810) + 4 * sin(second / 113)
        ram = 57 + self.offset / 2 + 5 * sin(second / 1200)
        process = float(int(second / 120) % 11 == 4)
        disk = self.disk_base + .5 * sin(second / 7200)
        for kind, start, duration in self.intervals:
            fraction = (second - start) / duration
            if not 0 <= fraction < 1:
                continue
            if kind in {"build", "short_build", "legitimate_cpu", "backup"}:
                cpu += 53 if kind != "backup" else 32
                ram += 10
                if int((second - start) / 120) % 7 == 1:
                    process += 3
            elif kind == "interactive":
                cpu += 38 if int(second / 70) % 3 else 8
            elif kind == "launch_stall":
                process += 3 if int((second - start) / 85) % 3 == 1 else 0
            elif kind.startswith("cpu_"):
                ramp = min(1., fraction * (1.75 + .18 * self.variant))
                cpu = max(cpu, 58 + 40 * ramp + (3 * sin(second / 130) if kind == "cpu_oscillation" else 0))
                ram = max(ram, 74 + 15 * ramp)
            elif kind == "process_ramp":
                process = max(process, float(min(6, int(1 + fraction * (6 + .2 * self.variant)))))
            elif kind == "process_step":
                process = max(process, 5. if fraction >= .32 else (2. if self.variant < 2 and fraction >= .18 else 0.))
                cpu += 12 if self.variant % 2 == 0 else 0
            elif kind == "disk_linear":
                disk = self.disk_base + (4 - self.disk_base) * fraction
            elif kind == "disk_accelerating":
                disk = self.disk_base + (7 - self.disk_base) * fraction ** (1.4 + self.variant / 4)
            elif kind == "disk_stable_near":
                disk = 13 + .45 * sin(second / 800)
            elif kind == "disk_temporary":
                disk = self.disk_base - (self.disk_base - 15) * sin(fraction * 3.141592653589793)
        return dict(zip(METRICS, (min(100., cpu), min(94., ram), disk, process), strict=True))


def latent_events(device, start, targets):
    detectors = {key: Actionable(key, targets.targets[key]) for key in SYSTEMS}
    active, events = {}, []
    for second in range(0, int(device.span), 15):
        time = start + second
        values = device.values(second)
        for key, tracker in detectors.items():
            change = tracker.update(time, values, time, True)
            if change == "actionable":
                active[key] = time
            elif change == "recovered":
                events.append(Event(f"{key}-{len(events) + 1}", key, active.pop(key), time))
    for key, onset in active.items():
        events.append(Event(f"{key}-{len(events) + 1}", key, onset, start + device.span))
    return sorted(events, key=lambda event: event.onset)


def generate_run(config, targets, seed, path):
    rng = Random(seed + 905)
    start_dt = datetime(2026, 5, 1, tzinfo=timezone.utc) + timedelta(days=seed % 49)
    start = start_dt.timestamp()
    device = Device(seed, config.hours_per_run)
    cadence = rng.choice((225, 300, 420))
    base = GeneratorConfig(days=config.hours_per_run / 24, random_seed=seed, start_time=start_dt,
                           scenarios=(), deep_interval_seconds=cadence)
    records = []
    cached = None
    due = 0.
    second = 0.
    previous = None
    while second < device.span:
        if not (device.sleep[0] <= second < device.sleep[1]) and (previous is None or rng.random() >= .008):
            now = start_dt + timedelta(seconds=second)
            values = device.values(second)
            readings = {}
            for metric in METRICS:
                if metric == "disk_free_pct":
                    if second >= due:
                        due = second + cadence
                        if not device.disk_gap[0] <= second < device.disk_gap[1]:
                            cached = (values[metric], now)
                    if cached is None or (now - cached[1]).total_seconds() >= 660:
                        readings[metric] = MetricReading(value=None, observation_time=None,
                                                        availability="unavailable", quality="missing")
                    else:
                        readings[metric] = MetricReading(value=cached[0], observation_time=cached[1],
                            availability="available", quality="fresh" if cached[1] == now else "cached")
                elif rng.random() < .002:
                    readings[metric] = MetricReading(value=None, observation_time=None,
                                                    availability="unavailable", quality="missing")
                else:
                    readings[metric] = MetricReading(value=values[metric], observation_time=now,
                                                    availability="available", quality="fresh")
            records.append(TelemetryRecord(source="synthetic", source_version=__version__, sample_id=f"p5-{seed}-{len(records)}",
                                          sample_time=now, nominal_interval_seconds=30,
                                          elapsed_seconds=None if previous is None else (now-previous).total_seconds(),
                                          ac_online=second / 3600 % 12 < 9, power_plan="balanced", metrics=readings))
            previous = now
        second = round(second + 30 + rng.uniform(-4, 4), 6)
    window = TelemetryWindow(window_start=records[0].sample_time, window_end=records[-1].sample_time,
                             as_of=records[-1].sample_time, records=tuple(records))
    write_dataset(GeneratedDataset(window, EvaluationTruth(scenarios=(), observations=()), base), path)
    write_json(path / "future_truth.json", dict(events=[asdict(event) for event in latent_events(device, start, targets)],
               workloads=[dict(kind=kind, start=start + low, end=start + low + duration)
                          for kind, low, duration in device.intervals if kind in {"build", "backup", "short_build"}]))


def prepare(root, config, targets):
    output_directory(root)
    manifest = dict(definition=experiment_definition(config, targets), runs=[])
    for split, seeds in (("development", config.development_seeds), ("validation", config.validation_seeds),
                         ("holdout", config.holdout_seeds)):
        for seed in seeds:
            name = f"{split}-{seed}"
            generate_run(config, targets, seed, root / name)
            manifest["runs"].append(dict(run=name, seed=seed, split=split, sha256={
                file: sha256(root / name / file)
                for file in ("manifest.json", "telemetry.csv", "scenarios.json", "future_truth.json")
            }))
    write_json(root / "locked_inputs.json", manifest)
    return sha256(root / "locked_inputs.json")


def open_lock(root, config, targets, digest):
    path = root / "locked_inputs.json"
    if sha256(path) != digest:
        raise ValueError("input lock hash mismatch")
    manifest = json.loads(path.read_text(encoding="utf8"))
    if manifest["definition"] != experiment_definition(config, targets):
        raise ValueError("experiment or source contract changed after lock")
    expected = [(f"{split}-{seed}", split, seed) for split, seeds in (
        ("development", config.development_seeds), ("validation", config.validation_seeds),
        ("holdout", config.holdout_seeds)) for seed in seeds]
    if [(run["run"], run["split"], run["seed"]) for run in manifest["runs"]] != expected:
        raise ValueError("unexpected locked split")
    if len({item["sha256"]["telemetry.csv"] for item in manifest["runs"]}) != len(expected):
        raise ValueError("telemetry reused across runs")
    return manifest


def check(root, run, file):
    path = root / run["run"] / file
    if sha256(path) != run["sha256"][file]:
        raise ValueError(f"locked file changed: {run['run']}/{file}")
    return path


def telemetry(root, run):
    check(root, run, "manifest.json")
    check(root, run, "telemetry.csv")
    return load_telemetry(root / run["run"])


def labels(root, run):
    check(root, run, "scenarios.json")
    data = json.loads(check(root, run, "future_truth.json").read_text(encoding="utf8"))
    return [Event(**event) for event in data["events"]], data["workloads"]
