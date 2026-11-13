"""Measure Phase 6 requests on generated fixtures without touching prior holdouts."""

import json
import tracemalloc
from statistics import median
from time import perf_counter

from fastapi.testclient import TestClient
import numpy as np

from test_phase6_service import HEADERS, SECRET, document, fixture
from ultron_health_ml.service.app import create_app, process


def describe(values):
    return dict(median_ms=median(values), p95_ms=float(np.quantile(values, .95)), max_ms=max(values))


def benchmark(size):
    results = []
    body = json.dumps(document(fixture(size)), separators=(",", ":")).encode()

    def measured(payload, policy, deadline, clock):
        timing = {"rows": []}
        response = process(payload, policy, deadline, clock, timing)
        results.append(timing)
        return response

    with TestClient(create_app(SECRET, processor=measured), base_url="http://127.0.0.1:8765",
                    client=("127.0.0.1", 1000)) as client:
        headers = {**HEADERS, "Content-Type": "application/json"}
        client.post("/v1/infer", content=body, headers=headers).raise_for_status()
        results.clear()
        end_to_end = []
        for _ in range(12):
            started = perf_counter()
            response = client.post("/v1/infer", content=body, headers=headers)
            response.raise_for_status()
            end_to_end.append((perf_counter() - started) * 1000)
        rows = [row for result in results for row in result["rows"]]
        timing = {name: describe([row[index] for row in rows]) for index, name in enumerate(
            ("features", "detection", "forecast", "lifecycle_and_findings"))}
        timing.update(incremental=describe([sum(row) for row in rows]), asgi_request=describe(end_to_end))
        for key in ("parse_ms", "infer_ms", "serialize_ms"):
            timing[key] = describe([result[key] for result in results])
        tracemalloc.start()
        try:
            client.post("/v1/infer", content=body, headers=headers).raise_for_status()
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
    return dict(rows=size, request_bytes=len(body), response_bytes=len(response.content), python_peak_bytes=peak,
                timings=timing)


if __name__ == "__main__":
    print(json.dumps([benchmark(size) for size in (120, 600, 1440)], indent=2))
