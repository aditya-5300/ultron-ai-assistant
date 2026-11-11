"""Build distinct B fixtures without opening or writing the A holdout."""

from datetime import timedelta
import json
from pathlib import Path
from random import Random
from tempfile import TemporaryDirectory

from ultron_health_ml.contracts.telemetry import REAL_ULTRON_METRICS, MetricReading, TelemetryWindow
from ultron_health_ml.data_generation.config import GeneratorConfig
from ultron_health_ml.data_generation.generator import GeneratedDataset, write_dataset
from ultron_health_ml.features.validation import load_evaluation_labels, load_telemetry
from ultron_health_ml.hybrid.datasets import (
    PROTOCOL as INPUT_SCHEMA, empty_directory, generate_run, settings_digest, verify_lock,
)
from ultron_health_ml.models.artifact import write_json
from ultron_health_ml.models.datasets import sha256
from ultron_health_ml.nuisance.protocol import OBJECTIVE, POLICIES, PROTOCOL


def new_output(path):
    path = path.resolve()
    repo = Path(__file__).resolve().parents[4]
    for frozen in (repo / "ml/generated/phase45", repo / "ml/artifacts/phase45"):
        if path == frozen or frozen in path.parents:
            raise ValueError("Phase 4.5-A outputs are read-only")
    empty_directory(path)


def contract():
    return dict(protocol=PROTOCOL, objective=OBJECTIVE, policies=POLICIES, implementation_sha256={
        name: sha256(Path(__file__).with_name(name))
        for name in ("datasets.py", "policy.py", "protocol.py", "replay.py")
    })


def variation(metric, observed, event, variant):
    elapsed = observed - event["event_start"]
    span = event["event_end"] - event["event_start"]
    if not 0 <= elapsed < span:
        return 0.
    kind = event["kind"]
    fraction = elapsed / span
    if metric == "memory_usage_pct":
        if kind == "build":
            return min(1., fraction * (4 + variant)) * (5 + variant * 2.)
        if kind in {"temporary_memory", "stable_high_memory"}:
            return (min(1., 5 * fraction) if kind == "temporary_memory" else 1.) * (2. + variant)
        if kind == "backup":
            return 4. + variant
    if metric == "selected_process_nonresponding_count" and kind in {
        "build", "short_build", "application_burst", "legitimate_high_cpu", "temporary_memory",
    }:
        if 25 * variant <= elapsed < 25 * variant + (45 + 20 * variant):
            return 2. + variant % 3
    if metric == "cpu_usage_pct" and kind in {"build", "backup", "legitimate_high_cpu"}:
        return (2 + variant) * (1 if int(elapsed / 90) % 2 else -1)
    return 0.


def write_run(settings, split, index, seed, output):
    with TemporaryDirectory(dir=output.parent, prefix=".b-fixture-") as stage:
        source = Path(stage) / "base"
        generate_run(settings, split, index, seed, source)
        records = load_telemetry(source).window.records
        events = json.loads((source / "events.json").read_text(encoding="utf-8"))
        settings_json = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
        config = GeneratorConfig.model_validate(settings_json["config"])
        labels = load_evaluation_labels(source)
        variant = Random(seed + 117).randrange(1, 6)
        rewritten = []
        for record in records:
            metrics = {}
            for metric, reading in record.metrics.items():
                if reading.value is None:
                    metrics[metric] = reading
                    continue
                observed = reading.observation_time.timestamp()
                value = reading.value + sum(variation(metric, observed, event, variant) for event in events)
                lower, upper, *_ = REAL_ULTRON_METRICS[metric]
                value = max(lower, min(value, upper)) if upper is not None else max(lower, value)
                if metric.endswith("_count"):
                    value = float(round(value))
                metrics[metric] = MetricReading(value=value, observation_time=reading.observation_time,
                                                quality=reading.quality, availability=reading.availability)
            rewritten.append(record.model_copy(update={"metrics": metrics}))
        window = TelemetryWindow(window_start=rewritten[0].sample_time, window_end=rewritten[-1].sample_time,
                                 as_of=rewritten[-1].sample_time, records=tuple(rewritten))
        write_dataset(GeneratedDataset(window, labels, config), output)
        write_json(output / "events.json", events)


def prepare(path, config):
    new_output(path)
    lock = dict(protocol=INPUT_SCHEMA, policy=contract(), settings_sha256=settings_digest(config), runs=[])
    for split, seeds in (("train", config.train_seeds), ("validation", config.validation_seeds),
                         ("holdout", config.holdout_seeds)):
        for index, seed in enumerate(seeds):
            name = f"{split}-{seed}"
            directory = path / name
            write_run(config, split, index, seed, directory)
            lock["runs"].append(dict(run=name, split=split, seed=seed, sha256={
                filename: sha256(directory / filename)
                for filename in ("manifest.json", "telemetry.csv", "scenarios.json", "events.json")
            }))
    write_json(path / "locked_inputs.json", lock)
    return sha256(path / "locked_inputs.json")


def locked(path, config, digest):
    lock = verify_lock(path, config, digest)
    canonical = json.loads(json.dumps(contract()))
    if lock.get("policy") != canonical:
        raise ValueError("policy or event-scoring implementation changed after data lock")
    return lock
