# ULTRON Phase 6 Local Inference Service

**Recommendation: GO TO PHASE 7 CONTRACT INTEGRATION**, for separately approved adapter contract work only. The service was developed and tested on isolated fixtures; it was not connected to ULTRON, production SQLite, the Windows worker, or the dashboard. No live inference, alerts or remediation were enabled. Phase 7 has not started.

## Service and security

The local FastAPI service parses bounded Phase 2 telemetry, replays Phase 3 features, invokes the frozen Phase 4.5-C reduced hybrid and the frozen Phase 5 process policy, and returns typed advisory events. It never reads or writes the production database, controls applications, or issues instructions. Node remains the owner of production monitoring and all deterministic safety actions. A critical result from Python is a **reference**, never an authoritative alarm. The independent production path does not call this service and remains unaffected if it is stopped, slow, or faulty.

The supported launcher binds **IPv4 `127.0.0.1` only**, default port **8765**, with a configurable nonprivileged port and no host override. It disables proxy-header trust, access logs, WebSockets, and interactive API docs. The app separately verifies the peer and numeric loopback Host, rejects browser Origin and Fetch Metadata headers, and sends no CORS headers. Do not expose it through a reverse proxy, LAN listener, or tunnel. A local process with the shared token is trusted to submit sanitized telemetry; loopback does not protect against a compromised OS account. No encryption over non-loopback links is provided or authorized.

| Endpoint | Authentication | Result |
|---|---|---|
| `GET /healthz` | Loopback only | Minimal status `ok` or `unavailable` |
| `GET /v1/metadata` | Local token | API/schema versions, fingerprints, approved scope and limits |
| `POST /v1/infer` | Local token | Bounded replay result, or fixed error code |

Use a distinct, randomly generated 32-byte base64url token per installation. In a developer PowerShell session (do not enable shell transcription or debug HTTP logging):

```powershell
python -m pip install -e "./ml[service,test]"
$env:ULTRON_INFERENCE_TOKEN = (python -c "import secrets; print(secrets.token_urlsafe(32))")
python -m ultron_health_ml.service --port 8765
```

Send the token in `X-ULTRON-Service-Token`, not in a URL or browser code. A future Node adapter should read the same secret from its private process environment or an OS-protected secret store; provisioning and rotation belong to the operator. Do not persist it in checked-in files. Stop the standalone process and clear the session variable when done. Tokens are checked with a constant-time comparison and are not included in responses, logs or generated results.

## Contract and replay

Requests are strict JSON with `api_version`, `feature_schema_version`, `feature_fingerprints` (the `detection` and `forecast` fingerprints from metadata), `request_id`, `replay_id`, `mode: "bounded_replay"`, `scope: {detection, forecasting}`, and a Phase 2 `telemetry` window containing `telemetry_schema_version`, `window_start`, `window_end`, `as_of` and `records`. All three schema versions are `1.0.0`. IDs are bounded ASCII identifiers. Scope accepts CPU, disk capacity, and process for detection and only process for forecasting; neither CPU nor disk is enabled for forecasts. The first/last acquisitions must equal the bounds, the last must equal as-of, and the first elapsed interval must be null. Historical elapsed times must agree with UTC timestamps. Wall-clock future tolerance is 2 s, but acquisitions and measurements after as-of are never allowed. Labels, unknown fields, conflicting cached readings, duplicate IDs, nonfinite values, synthetic-only metric names and out-of-order timestamps are rejected. No interpolation or fill occurs.

The first hour of replay is advisory warmup (reference critical detections can still appear). Supply at least one hour of history, plus enough fresh coverage for each detector's windows and persistence. A two-record request is structurally valid, not necessarily analytically sufficient. Short or missing history produces explicit `INSUFFICIENT_EVIDENCE`, not a fabricated healthy result. A replay is self-contained, with no sessions, state persistence, or idle cleanup. Restarting the service changes no decision. A future caller must retain the replay namespace and starting history to reuse IDs across growing prefixes; changing the origin, scope, or history may change decisions. Client request IDs do not contribute to logical identity.

Each finding provides `finding_id`, `event_id`, subsystem, kind, transition, state, severity, first/evidence/decision timestamps, source, definition version, and quality/evidence. Forecast-origin events retain the original horizon and target window when they transition to detection. Scores are explicitly uncalibrated evidence, not probabilities or failure predictions. A history response can contain OPEN, UPDATE, ESCALATE, CLOSE and INSUFFICIENT_EVIDENCE; historical OPEN events are not instructions to send a notification now. A future adapter must track consumed event IDs and never treat UPDATE as a new OPEN. Absence of a finding is not a healthy verdict.

## Frozen engines and ownership

The bundled policy is hash-checked and uses Phase 4.5-C's hybrid threshold **1.35**, persistence multiplier **2**, and Phase 5's `persistence:1800:0.65` with minimum useful lead **240 s**. Phase 3's full and forecasting feature configurations are replayed separately to preserve both fingerprints. The process tracker uses the original Phase 5 actionable transition and persistence score. No classifier is trained, and nothing predicts application crashes, OS failures, or hardware failure.

Repeated process confirmations update one owner. An active process forecast can transition to detection with the same finding ID and an ESCALATE event, rather than a new OPEN. Closure requires fresh recovery; missing data cannot close a finding. Recurrence after recovery opens a new ID. Service-only differences from raw engine output are the one-hour advisory warmup, selected-scope filtering, explicit coverage events, the combination of detector and forecast owners, and omission of RAM critical findings from the public scope. The detector's reference critical pass still runs. Forecasting-only requests can update a forecast that becomes actionable, but cannot open an unrelated process detection. Future notification routing is not implemented.

**The 12-duplicate release caveat remains open.** Counting the Phase 4.5-C duplicate process notices as unwanted notifications raises the rate to about **1.48/device-day**, over the 0.5 limit. Stable replay IDs and offline ownership tests do not demonstrate live suppression. Validate durable deduplication, replay cursor handling, and critical independence at the future adapter boundary before any user-facing deployment.

## Limits and errors

- One Uvicorn worker, eight server concurrency slots and a single admitted body/replay worker. Busy requests fail rather than queue. Requests cap at **2 MiB**, **1,800 observations** and **12 hours**; the service does not truncate history. Responses cap at **5 MiB**.
- Body timeout is **3 s**. Parse, replay and serialization have a **12 s** response deadline. Replay checks the deadline between rows. Timeout does not forcibly kill a Python thread: capacity stays reserved until it returns. This is bounded admission, not an OS-level memory limit or process sandbox. Test-server proxy headers are ignored in the supported launcher.
- Errors include **401 UNAUTHORIZED**, **403 FORBIDDEN**, **413 REQUEST_TOO_LARGE**, **415/422 INVALID_REQUEST**, **422 UNSUPPORTED_VERSION / INCOMPATIBLE_POLICY**, **503 INCOMPATIBLE_POLICY / SERVICE_UNAVAILABLE**, and **500 INTERNAL_ERROR**. Missing or thin evidence is a structured finding in an otherwise valid replay. Public errors contain fixed codes only, no submitted values, paths, exceptions or tokens.

## Verification and performance

**234 Python tests passed**, including 23 Phase 6 cases. New in-memory fixtures covered normal and sustained load, disk decline, cached samples, sleep-like gaps, repeated process evidence, forecast-to-detection ownership, cancellation, missing data and recurrence. Security checks covered malformed and oversized requests (including streamed overflow), authorization, remote peer and browser-origin rejection, strict versions, bounded time, and exception redaction. Replay checks compared the API against direct Phase 4.5/5 invocations, verified deterministic independent clients, preserved identities across prefixes, and verified independent reference safety. A real launcher smoke test used an ephemeral token and temporary local port: one IPv4 loopback listener, successful authenticated inference, rejected browser origin, no token in logs, and listener stopped after the check.

Benchmark: 12 measured full requests per fixture after warmup on local Windows, via in-process HTTP/ASGI (no TCP); Python traced peak allocation includes request processing, not total RSS. Latencies are milliseconds. The separate 220-row loopback smoke request took **273.67 ms**. Engineering targets are **p95 <100 ms per incremental row** and **p95 <5 s for a representative full replay up to 1,440 rows** on this workstation, not a production SLA.

| Rows | Request bytes | Full request median / p95 / max | Incremental median / p95 / max | Python peak |
|---|---:|---:|---:|---:|
| 120 | 95,260 | 112 / 120 / 123 | 0.86 / 1.19 / 8.26 | 1.39 MB |
| 600 | 474,109 | 886 / 1,338 / 1,655 | 1.22 / 2.64 / 310.04 | 5.99 MB |
| 1,440 | 1,135,545 | 1,664 / 1,916 / 1,932 | 1.00 / 1.53 / 128.95 | 13.79 MB |

For 1,440 rows, median / p95 / max were **48.10 / 201.28 / 236.78 ms** for request validation and **0.85 / 2.41 / 3.80 ms** for response serialization. Per-row medians for features, detection, forecasting, and finding translation were **0.906 / 0.052 / 0.029 / 0.010 ms**, respectively. Maximum row latency can exceed the p95 target. Run the benchmark from `ml` with `PYTHONPATH=src` and `python tests/benchmark_phase6_service.py`. The test client emitted one upstream HTTPX deprecation warning.

All **514** prior reference hashes verified; no prior holdout was replayed or regenerated for this phase. The health fixture, command/agent/browser-tab/native-speech self-checks, Next.js production build, TypeScript and Git whitespace checks passed. Voice checks used generated audio, not a live microphone.

**Files:** Added `ml/src/ultron_health_ml/service/` (launcher, transport, schemas, bundled policy and replay engine), `ml/tests/test_phase6_service.py`, `ml/tests/benchmark_phase6_service.py`, and this report. Updated `ml/pyproject.toml` for optional FastAPI/Uvicorn and HTTPX test dependencies and package data, plus the ML README. No production worker, SQLite, API, dashboard, controller or LLM/RAG changes. Nothing was committed, pushed or merged.

Framework reference: [FastAPI documentation URL settings](https://fastapi.tiangolo.com/tutorial/metadata/).
