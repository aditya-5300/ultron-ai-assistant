"""Phase 6 synthetic API fixtures; never read any frozen holdout."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from threading import Event
from time import monotonic

import pytest
from fastapi.testclient import TestClient

from feature_helpers import at, telemetry
from ultron_health_ml.features.config import FeatureConfig
from ultron_health_ml.features.engineering import iter_features
from ultron_health_ml.forecast.lifecycle import Point as ForecastPoint
from ultron_health_ml.forecast.replay import replay
from ultron_health_ml.reduced.policy import ReducedReplay, common_signals
from ultron_health_ml.service import app as api
from ultron_health_ml.service.engine import infer
from ultron_health_ml.service.policy import approved
from ultron_health_ml.service.schema import DETECTION_FP, FORECAST_FP, InferRequest, InferResponse

SECRET = "a" * 43
HEADERS = {"X-ULTRON-Service-Token": SECRET}


def fixture(size=220, *, recurrence=False):
    readings = []
    for index in range(size):
        stamp = index * 30
        process = 2 if 3900 <= stamp < 4380 else 5 if 4380 <= stamp < 5100 else 0
        if recurrence and 6000 <= stamp < 6480:
            process = 2
        elif recurrence and 6480 <= stamp < 7200:
            process = 5
        if stamp == 4260:
            process = None
        disk_stamp = stamp // 300 * 300
        disk = max(3., 50. - max(0, disk_stamp - 2700) / 65)
        readings.append((stamp, {"cpu_usage_pct": 92. if 4380 <= stamp < 5400 else 36.,
            "memory_usage_pct": 88. if 4380 <= stamp < 5400 else 55.,
            "selected_process_nonresponding_count": float(process) if process is not None else None,
            "disk_free_pct": (disk, disk_stamp, "fresh" if disk_stamp == stamp else "cached")}))
    return telemetry(readings)


def document(window=None):
    window = window or fixture()
    return dict(api_version="1.0.0", feature_schema_version="1.0.0",
                feature_fingerprints=dict(detection=DETECTION_FP, forecast=FORECAST_FP),
                request_id="req1", replay_id="stream1", mode="bounded_replay",
                scope=dict(detection=["cpu", "disk_capacity", "process"], forecasting=["process"]),
                telemetry=window.model_dump(mode="json"))


def client(app=None, remote="127.0.0.1"):
    return TestClient(app or api.create_app(SECRET), base_url="http://127.0.0.1:8765", client=(remote, 1000))


def payload(doc):
    return InferRequest.model_validate_json(json.dumps(doc), strict=True)


def test_frozen_policy_matches_reference_and_packaged_contract():
    repo = Path(__file__).resolve().parents[2]
    frozen = json.loads((repo / "docs/phase5-reference/horizon_selection.json").read_bytes())
    settings, point, targets, forecast, noise = approved()
    assert frozen["selected"]["process"] == vars(forecast)
    assert frozen["training_noise_floors"]["process"] == noise
    assert targets.model_dump(mode="json") == json.loads((repo / "ml/configs/forecast_target_contract.json").read_bytes())
    assert vars(point) == json.loads((repo / "docs/phase45c-reference/frozen_selection.json").read_bytes())["points"]["hybrid"]
    assert settings.subsystems["disk_capacity"].metric == "disk_free_pct"


def test_transport_auth_browser_remote_and_metadata(caplog):
    with client() as local:
        assert local.get("/healthz").json() == {"status": "ok"}
        assert local.get("/docs").status_code == 404
        assert local.get("/openapi.json").status_code == 404
        for headers in ({}, {"X-ULTRON-Service-Token": "wrong"}, [*HEADERS.items(), *HEADERS.items()]):
            response = local.post("/v1/infer", json=document(), headers=headers)
            assert response.status_code == 401 and response.json() == {"error": "UNAUTHORIZED"}
        for headers in ({"Origin": "http://127.0.0.1:3000"}, {"Host": "example.test"}, {"Sec-Fetch-Site": "same-origin"}):
            assert local.post("/v1/infer", json=document(), headers=HEADERS | headers).status_code == 403
        cors = local.options("/v1/infer", headers={"Origin": "http://127.0.0.1", "Access-Control-Request-Method": "POST"})
        assert cors.status_code == 403 and "access-control-allow-origin" not in cors.headers
        assert local.get("/v1/metadata").status_code == 401
        assert local.get("/v1/metadata", headers=HEADERS).json()["forecasting_scope"] == ["process"]
    with client(remote="192.0.2.1") as remote:
        assert remote.get("/healthz", headers={"X-Forwarded-For": "127.0.0.1"}).status_code == 403
    assert SECRET not in caplog.text


@pytest.mark.parametrize("mutation,expected", [
    (lambda d: d.update(api_version="2"), "UNSUPPORTED_VERSION"),
    (lambda d: d.update(feature_schema_version="2"), "UNSUPPORTED_VERSION"),
    (lambda d: d["telemetry"].update(telemetry_schema_version="2"), "UNSUPPORTED_VERSION"),
    (lambda d: d["feature_fingerprints"].update(forecast="wrong"), "INCOMPATIBLE_POLICY"),
    (lambda d: d.update(labels=["future"]), "INVALID_REQUEST"),
    (lambda d: d["scope"].update(forecasting=["cpu"]), "INVALID_REQUEST"),
    (lambda d: d["telemetry"]["records"][0]["metrics"].update(invalid_metric={}), "INVALID_REQUEST"),
    (lambda d: d["telemetry"]["records"][1].update(elapsed_seconds=-1), "INVALID_REQUEST"),
    (lambda d: d["telemetry"]["records"][1].update(sample_id="fixture-0"), "INVALID_REQUEST"),
    (lambda d: d["telemetry"]["records"].reverse(), "INVALID_REQUEST"),
    (lambda d: d["telemetry"].update(as_of="2020-01-01T00:00:00Z"), "INVALID_REQUEST"),
    (lambda d: d["telemetry"]["records"][1].update(sample_time="bad"), "INVALID_REQUEST"),
    (lambda d: d["telemetry"]["records"][0]["metrics"]["cpu_usage_pct"].update(value=float("nan")), "INVALID_REQUEST"),
])
def test_strict_contract(mutation, expected):
    doc = document(fixture(4))
    mutation(doc)
    with client() as local:
        result = local.post("/v1/infer", content=json.dumps(doc), headers=HEADERS | {"Content-Type": "application/json"})
    assert result.status_code == 422 and result.json() == {"error": expected}


def test_malformed_body_size_stream_row_limit_and_wall_clock():
    def reject(*_):
        pytest.fail("oversized body reached parser")
    with client(api.create_app(SECRET, processor=reject)) as local:
        extra = b"x" * (api.MAX_BODY_BYTES // 2 + 1)
        for body in (extra + extra, iter((extra, extra))):
            response = local.post("/v1/infer", content=body, headers=HEADERS | {"Content-Type": "application/json"})
            assert response.status_code == 413
    with client(api.create_app(SECRET, clock=lambda: at(29))) as local:
        h = HEADERS | {"Content-Type": "application/json"}
        for invalid in (b"{", b'{"a":1,"a":2}', b'{"a":Infinity}'):
            assert local.post("/v1/infer", content=invalid, headers=h).json() == {"error": "INVALID_REQUEST"}
        assert local.post("/v1/infer", json=document(fixture(4)), headers=HEADERS).status_code == 422
        too_many = document(fixture(4))
        too_many["telemetry"]["records"] *= 500
        assert local.post("/v1/infer", json=too_many, headers=HEADERS).status_code == 413


def test_overlong_history_and_timestamp_gap():
    wide = telemetry([(0, {"cpu_usage_pct": 30.}), (43201, {"cpu_usage_pct": 30.})])
    with client() as local:
        assert local.post("/v1/infer", json=document(wide), headers=HEADERS).status_code == 422
        gap = telemetry([(0, {"cpu_usage_pct": 30.}), (3600, {"cpu_usage_pct": 30.})])
        result = local.post("/v1/infer", json=document(gap), headers=HEADERS)
        assert result.status_code == 200
        assert any(row["finding_type"] == "INSUFFICIENT_EVIDENCE" for row in result.json()["findings"])


def test_fresh_replay_parity_and_shared_process_ownership():
    window = fixture(270, recurrence=True)
    doc = document(window)
    config, point, targets, forecast, noise = approved()
    result = infer(payload(doc), approved(), monotonic() + 12)
    with client() as local, client() as independent:
        first = local.post("/v1/infer", json=doc, headers=HEADERS)
        assert first.status_code == 200, first.text
        assert first.json() == independent.post("/v1/infer", json=doc, headers=HEADERS).json()
        changed = independent.post("/v1/infer", json={**doc, "replay_id": "other"}, headers=HEADERS).json()
    assert first.json() == result.model_dump(mode="json")
    assert InferResponse.model_validate_json(first.content) == result
    assert not ({row.finding_id for row in result.findings} & {row["finding_id"] for row in changed["findings"]})

    direct = ReducedReplay(config, point)
    for row in iter_features(window, FeatureConfig()):
        direct.consume(row, lambda: common_signals(row))
    for episode in direct.episodes:
        if episode.subsystem in ("cpu", "disk_capacity"):
            assert any(row.subsystem == episode.subsystem and row.transition == "OPEN" and
                       row.first_at.timestamp() == episode.opened_at for row in result.findings)
    outputs, _, safety, _, _ = replay({"run": "stream1"}, type("Source", (), {"window": window, "dataset_version": None})(),
        targets, dict(cpu=.5, disk_capacity=.12, process=noise), (forecast,))
    baseline = outputs[forecast.name]
    assert baseline["raw"] == result.process_raw_qualifying_rows and safety["mismatches"] == 0
    process = [row for row in result.findings if row.subsystem == "process"]
    opened = [row for row in process if row.transition == "OPEN"]
    assert len(opened) == len(baseline["episodes"]) == 2
    assert [row.first_at.timestamp() for row in opened] == [episode.first_prediction for episode in baseline["episodes"]]
    for notice in opened:
        related = [row for row in process if row.finding_id == notice.finding_id]
        assert notice.finding_type == "FORECAST" and notice.minimum_useful_lead_seconds == 240
        assert any(row.transition == "ESCALATE" and row.finding_type == "DETECTION" for row in related)
        assert any(row.transition == "UPDATE" for row in related)
        assert any(row.transition == "CLOSE" for row in related)
    assert len({row.event_id for row in process}) == len(process)
    assert all(row.subsystem == "process" for row in result.findings if row.finding_type == "FORECAST")


def test_missing_and_cached_evidence_cannot_close_and_no_scope_expansion():
    with client() as local:
        doc = document(fixture(270, recurrence=True))
        output = local.post("/v1/infer", json=doc, headers=HEADERS).json()["findings"]
        missing = at(4260).isoformat().replace("+00:00", "Z")
        assert any(row["transition"] == "INSUFFICIENT_EVIDENCE" and row["decision_at"] == missing
                   for row in output)
        assert not any(row["transition"] == "CLOSE" and row["decision_at"] == missing for row in output)
        assert not any(row["transition"] == "UPDATE" and row["evidence"]["quality"] == "cached" for row in output)
        doc["scope"] = dict(detection=[], forecasting=["process"])
        assert {row["subsystem"] for row in local.post("/v1/infer", json=doc, headers=HEADERS).json()["findings"]} == {"process"}


def test_cancelled_forecast_closes_with_original_identity():
    readings = [(stamp, {"cpu_usage_pct": 30., "memory_usage_pct": 52., "disk_free_pct": 60.,
                         "selected_process_nonresponding_count": 2. if 3900 <= stamp < 4350 else 0.})
                for stamp in range(0, 5100, 30)]
    result = infer(payload(document(telemetry(readings))), approved(), monotonic() + 12)
    process = [row for row in result.findings if row.subsystem == "process"]
    opened = next(row for row in process if row.transition == "OPEN")
    closed = next(row for row in process if row.transition == "CLOSE")
    assert opened.finding_id == closed.finding_id and closed.finding_type == "FORECAST"
    assert closed.reason == "fresh_recovery"
    assert not any(row.transition == "ESCALATE" for row in process)


def test_as_of_prefix_is_independent_of_suffix():
    window = fixture()
    prefix = window.model_copy(update=dict(records=window.records[:155], window_end=window.records[154].sample_time,
                                          as_of=window.records[154].sample_time))
    before = infer(payload(document(prefix)), approved(), monotonic() + 12)
    after = infer(payload(document(window)), approved(), monotonic() + 12)
    assert tuple(row for row in after.findings if row.decision_at <= before.as_of) == before.findings


def test_safe_timeout_concurrency_and_internal_errors(monkeypatch, caplog):
    started, release = Event(), Event()
    def slow(*_):
        started.set()
        release.wait(5)
        return b"{}"
    monkeypatch.setattr(api, "INFERENCE_TIMEOUT", .10)
    with client(api.create_app(SECRET, processor=slow)) as local, ThreadPoolExecutor(max_workers=1) as pool:
        job = pool.submit(local.post, "/v1/infer", json=document(fixture(4)), headers=HEADERS)
        try:
            assert started.wait(3)
            assert local.post("/v1/infer", json=document(fixture(4)), headers=HEADERS).status_code == 503
            assert job.result(timeout=3).json() == {"error": "SERVICE_UNAVAILABLE"}
            assert local.post("/v1/infer", json=document(fixture(4)), headers=HEADERS).status_code == 503
        finally:
            release.set()
    def broken(*_):
        raise RuntimeError(SECRET + " private filesystem path")
    with client(api.create_app(SECRET, processor=broken)) as local:
        response = local.post("/v1/infer", json=document(fixture(4)), headers=HEADERS)
        assert response.status_code == 500 and response.json() == {"error": "INTERNAL_ERROR"}
    assert SECRET not in caplog.text


def test_launcher_has_fixed_loopback_and_ignores_forwarded_headers(monkeypatch):
    import sys
    from ultron_health_ml.service import __main__ as launcher
    options = {}
    monkeypatch.setattr(sys, "argv", ["service", "--port", "8766"])
    monkeypatch.setenv("ULTRON_INFERENCE_TOKEN", SECRET)
    monkeypatch.setattr(launcher.uvicorn, "run", lambda _, **kwargs: options.update(kwargs))
    launcher.main()
    assert options["host"] == "127.0.0.1" and options["port"] == 8766 and options["workers"] == 1
    assert options["proxy_headers"] is False and options["access_log"] is False
    monkeypatch.setattr(sys, "argv", ["service", "--host", "0.0.0.0"])
    with pytest.raises(SystemExit):
        launcher.main()
