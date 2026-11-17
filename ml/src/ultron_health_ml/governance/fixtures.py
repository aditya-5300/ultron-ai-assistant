"""Separate seeded Phase 9 telemetry splits; no private or historical telemetry."""

from datetime import datetime, timedelta, timezone
from random import Random

from ultron_health_ml.contracts.telemetry import TelemetryWindow

SPLITS = {"reference": 91701, "development": 91702, "locked": 91703}
SCENARIOS = ("stable", "shift", "missing", "cached", "constant", "sparse", "irregular", "context", "short")


def fixture(split, scenario):
    seed = SPLITS[split]
    if scenario not in SCENARIOS or (split == "reference" and scenario != "stable"):
        raise ValueError("unsupported fixture")
    start = datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(days=list(SPLITS).index(split) * 10)
    random = Random(seed)
    rows = []
    previous = None
    for index in range(20 if scenario == "short" else 180):
        step = 60 if scenario == "irregular" else 30
        offset = index * step + (600 if scenario == "irregular" and index >= 75 else 0)
        time = start + timedelta(seconds=offset)
        def fresh(value):
            return {"value": value, "observation_time": time, "availability": "available", "quality": "fresh"}
        metrics = {
            "cpu_usage_pct": fresh(65 + random.randrange(-4, 5) if scenario == "shift" else 35 + random.randrange(-4, 5)),
            "memory_usage_pct": fresh(48 if scenario == "constant" else 48 + random.randrange(-2, 3)),
            "disk_free_pct": fresh(50),
            "selected_process_nonresponding_count": fresh(int(index % (97 if scenario == "sparse" else 31) == 0)),
        }
        if scenario == "missing" and index % 4:
            metrics.pop("cpu_usage_pct")
        if scenario == "cached" and index:
            age = 690 if index % 3 == 0 else 60
            metrics["disk_free_pct"] = {"value": 50, "observation_time": time - timedelta(seconds=age),
                                        "availability": "available", "quality": "cached"}
        if scenario == "context":
            metrics["network_latency_ms"] = fresh(20)
        rows.append({"source": "synthetic", "source_version": "phase9-fixture-v1", "sample_id": f"{split}-{scenario}-{index}",
                     "sample_time": time, "nominal_interval_seconds": 30,
                     "elapsed_seconds": (time - previous).total_seconds() if previous else None,
                     "ac_online": scenario != "context", "power_plan": "saver" if scenario == "context" else "balanced",
                     "metrics": metrics})
        previous = time
    return TelemetryWindow.model_validate({"records": rows, "window_start": rows[0]["sample_time"],
                                           "window_end": previous, "as_of": previous})
