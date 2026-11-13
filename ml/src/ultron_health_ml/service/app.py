"""Authenticated, bounded FastAPI transport; never invokes production components."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import hmac
import json
import re
from time import monotonic, perf_counter

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError
from starlette.requests import ClientDisconnect

from ultron_health_ml.service.engine import infer
from ultron_health_ml.service.policy import approved
from ultron_health_ml.service.schema import (
    API_VERSION, DETECTION_FP, FORECAST_FP, MAX_BODY_BYTES, MAX_FUTURE_SECONDS, MAX_HISTORY_SECONDS,
    MAX_ROWS, POLICY_VERSION, InferRequest,
)

BODY_TIMEOUT = 3.
INFERENCE_TIMEOUT = 12.
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
TOKEN_HEADER = "x-ultron-service-token"
HOST = re.compile(r"127\.0\.0\.1(?::[0-9]{1,5})?")
TOKEN = re.compile(r"[A-Za-z0-9_-]{43,128}")


class RequestFailure(Exception):
    def __init__(self, status, code):
        self.status, self.code = status, code


def error(status, code):
    return JSONResponse({"error": code}, status_code=status, headers={"Cache-Control": "no-store"})


def unique_object(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def nonfinite(_):
    raise ValueError("nonfinite JSON")


def parse(payload, clock):
    try:
        document = json.loads(payload, object_pairs_hook=unique_object, parse_constant=nonfinite)
        window = document.get("telemetry", {}) if isinstance(document, dict) else {}
        if isinstance(window, dict) and isinstance(window.get("records"), list) and len(window["records"]) > MAX_ROWS:
            raise RequestFailure(413, "REQUEST_TOO_LARGE")
        request = InferRequest.model_validate_json(payload, strict=True)
    except ValidationError as exc:
        errors = exc.errors(include_input=False, include_context=False, include_url=False)
        if any(item["type"] == "literal_error" and item["loc"][-1] in
               {"api_version", "feature_schema_version", "telemetry_schema_version"} for item in errors):
            raise RequestFailure(422, "UNSUPPORTED_VERSION") from None
        if any(item["loc"][:1] == ("feature_fingerprints",) and item["type"] == "literal_error"
               for item in errors):
            raise RequestFailure(422, "INCOMPATIBLE_POLICY") from None
        raise RequestFailure(422, "INVALID_REQUEST") from None
    except (ValueError, RecursionError):
        raise RequestFailure(422, "INVALID_REQUEST") from None
    if request.telemetry.as_of > clock() + timedelta(seconds=MAX_FUTURE_SECONDS):
        raise RequestFailure(422, "INVALID_REQUEST")
    return request


def process(payload, policy, deadline, clock, timings=None):
    start = perf_counter()
    request = parse(payload, clock)
    parsed = perf_counter()
    try:
        response = infer(request, policy, deadline, timings.get("rows") if timings is not None else None)
    except ValueError:
        raise RequestFailure(422, "INVALID_REQUEST") from None
    inferred = perf_counter()
    encoded = response.model_dump_json().encode()
    if monotonic() >= deadline or len(encoded) > MAX_RESPONSE_BYTES:
        raise RequestFailure(503, "SERVICE_UNAVAILABLE")
    if timings is not None:
        timings.update(parse_ms=(parsed-start)*1000, infer_ms=(inferred-parsed)*1000,
                       serialize_ms=(perf_counter()-inferred)*1000)
    return encoded


def create_app(secret, *, clock=lambda: datetime.now(timezone.utc), processor=process):
    if not isinstance(secret, str) or not TOKEN.fullmatch(secret):
        raise ValueError("configure a valid generated local service token")
    token = secret.encode("ascii")
    try:
        policy = approved()
    except Exception:
        policy = None

    @asynccontextmanager
    async def lifespan(app):
        app.state.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ultron-replay")
        app.state.busy = False
        try:
            yield
        finally:
            app.state.executor.shutdown(wait=True, cancel_futures=True)

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None, debug=False,
                  redirect_slashes=False)

    @app.middleware("http")
    async def network_boundary(request, call_next):
        if request.client is None or request.client.host != "127.0.0.1":
            return error(403, "FORBIDDEN")
        hosts = request.headers.getlist("host")
        if (len(hosts) != 1 or not HOST.fullmatch(hosts[0]) or "origin" in request.headers
                or any(name.startswith("sec-fetch-") for name in request.headers)):
            return error(403, "FORBIDDEN")
        return await call_next(request)

    def authorized(request):
        presented = request.headers.getlist(TOKEN_HEADER)
        return (len(presented) == 1 and len(presented[0]) <= 128
                and hmac.compare_digest(presented[0].encode("latin-1"), token))

    @app.get("/healthz")
    async def health():
        return JSONResponse({"status": "ok" if policy else "unavailable"}, status_code=200 if policy else 503,
                            headers={"Cache-Control": "no-store"})

    @app.get("/v1/metadata")
    async def metadata(request: Request):
        if not authorized(request):
            return error(401, "UNAUTHORIZED")
        if policy is None:
            return error(503, "INCOMPATIBLE_POLICY")
        return JSONResponse(dict(api_version=API_VERSION, feature_schema_version="1.0.0",
                                 telemetry_schema_version="1.0.0", policy_version=POLICY_VERSION,
                                 feature_fingerprints=dict(detection=DETECTION_FP, forecast=FORECAST_FP),
                                 detection_scope=["cpu", "disk_capacity", "process"], forecasting_scope=["process"],
                                 max_records=MAX_ROWS, max_history_seconds=MAX_HISTORY_SECONDS,
                                 max_body_bytes=MAX_BODY_BYTES), headers={"Cache-Control": "no-store"})

    @app.post("/v1/infer")
    async def inference(request: Request):
        if not authorized(request):
            return error(401, "UNAUTHORIZED")
        if policy is None:
            return error(503, "INCOMPATIBLE_POLICY")
        if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
            return error(415, "INVALID_REQUEST")
        if request.headers.get("content-encoding", "identity").lower() != "identity":
            return error(415, "INVALID_REQUEST")
        lengths = request.headers.getlist("content-length")
        if lengths:
            if len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,18}", lengths[0]):
                return error(400, "INVALID_REQUEST")
            if int(lengths[0]) > MAX_BODY_BYTES:
                return error(413, "REQUEST_TOO_LARGE")
        if app.state.busy:
            return error(503, "SERVICE_UNAVAILABLE")
        app.state.busy = True
        dispatched = False
        try:
            payload = bytearray()
            async with asyncio.timeout(BODY_TIMEOUT):
                async for chunk in request.stream():
                    if len(payload) + len(chunk) > MAX_BODY_BYTES:
                        return error(413, "REQUEST_TOO_LARGE")
                    payload.extend(chunk)
            if lengths and int(lengths[0]) != len(payload):
                return error(400, "INVALID_REQUEST")
            deadline = monotonic() + INFERENCE_TIMEOUT
            loop = asyncio.get_running_loop()
            work = loop.run_in_executor(app.state.executor, processor, bytes(payload), policy, deadline, clock)

            def completed(future):
                app.state.busy = False
                if not future.cancelled():
                    future.exception()

            work.add_done_callback(completed)
            dispatched = True
            async with asyncio.timeout(INFERENCE_TIMEOUT):
                result = await asyncio.shield(work)
            return Response(result, media_type="application/json", headers={"Cache-Control": "no-store"})
        except RequestFailure as exc:
            return error(exc.status, exc.code)
        except TimeoutError:
            return error(503, "SERVICE_UNAVAILABLE")
        except ClientDisconnect:
            return error(400, "INVALID_REQUEST")
        except Exception:
            return error(500, "INTERNAL_ERROR")
        finally:
            if not dispatched:
                app.state.busy = False

    return app
