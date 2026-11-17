# ULTRON Phase 9-H HTTP Boundary Reliability

**Decision: HTTP RELIABILITY VERIFIED** for the authenticated Phase 6 client and service in the tested loopback matrix. The legacy raw upload did not have a zero failure rate. The original post-integration failure remains a failure.

## Original failure and scope

The approved baseline is `3c851d6f28278acc2c7520180130736a70b9f902`. The post-integration Node test recorded `read ECONNRESET` without a request identifier. The byte-identical TAP is archived locally outside Git; `phase9h-reference/original_failure_reference.json` contains its SHA-256, a sanitized excerpt, the original limits, and hashes of the baseline test, configuration, client, and service. Later runs are separate. The exact historical request cannot be recovered from that TAP.

The investigation used Windows, Node 24.18.0, Python 3.13.12, FastAPI 0.141.1, and Uvicorn 0.52.1. Each predeclared repetition used a new isolated listener on IPv4 loopback, an ephemeral fixture token, and a separate temporary seeded SQLite database. It called the actual Node inference client and the unchanged Python launcher and application. The fixture instruments Node connection and response events and observes Python ASGI upload, response, and processing boundaries. It does not log tokens, socket addresses, private records, or request bodies.

## Boundary matrix and recovery

| Case | Per-run input | Observed in five runs |
| --- | --- | --- |
| Below cap | Valid 480-observation replay | Five complete 200 responses |
| Maximum observations | 1,800 observations | Five complete 200 responses |
| Near size limit | 2,088,960 bytes | Five complete 200 responses |
| Exact size limit | 2,097,152 bytes | Five complete 200 responses |
| One byte above, actual client | 2,097,153 bytes | Five HTTP 413 responses mapped to `CONTRACT_REJECTED` |
| Near time limit | 43,170 seconds | Five complete 200 responses |
| Exact time limit | 43,200 seconds | Five complete 200 responses |
| Above time limit | 43,201 seconds | Five HTTP 422 responses mapped to `CONTRACT_REJECTED` |
| Repeated valid inference | Same validated input and identity | Five matching replays on new connections |
| Legacy full upload | 2,097,153 bytes declared and sent immediately | Three complete 413 responses; two resets after 413 headers |
| Corrected raw contract probe | Same declared size, headers only with `Expect: 100-continue` | Five complete 413 responses with `REQUEST_TOO_LARGE`; no upload invited |

All supported requests received the full declared input at the service. The normal, near-limit, and exact-limit versions of the same 1,800-observation replay returned identical validated results; unsupported history was rejected rather than truncated. All 20 recovery checks passed: the same listener remained alive, authenticated metadata matched the contract, and a new valid inference produced the original replay response and identity. No actual notifications were sent. Repetition, request, connection, timing, and recovery rows are included in `phase9h-reference/`.

## What reset

In repetitions 4 and 5, the legacy raw full-upload probe, inference request sequence 25, reproduced `ECONNRESET`. The service returned 413 without reading the oversized body, and the raw client received the status headers but not a complete response before the reset. The listener was alive and handled the immediate authenticated recovery. The same request was rejected normally by the actual Node client, which consumes the non-200 status and destroys the response rather than relying on its body. It does not reuse sockets.

The evidence supports a transport race in the original raw fixture's assumption that it can upload an already-rejected body and always read the complete error body. It does not establish the exact OS-level packet order or prove which request failed historically. It does not indicate a readiness, timeout, service-exit, or request-limit bypass problem. The corrected raw check declares the oversized request and waits for the decision before sending bytes. The old raw path remains a separately recorded diagnostic. The frozen Phase 8 test is not edited, erased, or counted as passing here. The five-run Phase 9-H test supplies its request-boundary coverage in the focused validation set.

## Deadlines and listener lifecycle

Readiness took 0.81–2.24 seconds. During successful inference the maximum connection time was 20.58 ms; maximum client upload handoff was 22.55 ms and maximum Python upload receipt was 26.44 ms. Processing through response headers peaked at 3,481.84 ms; the processor alone peaked at 3,479.05 ms. Maximum client response transfer after headers was 1.17 ms and maximum total inference request time was 3,498.47 ms. These are stage maxima across local requests, not a single request's timeline. No configured deadline fired. Node still uses 1,200 ms to connect and 15,000 ms total per request; Python still allows 3,000 ms for the body and 12,000 ms for processing. The raw fixture uses the unchanged 15,000 ms idle timeout. Client total measurements exclude the separate metadata request.

Fixture shutdown happened only after request and recovery checks completed. All five listeners were alive before planned termination; no listener exited unexpectedly. ASGI response completion only confirms the application handed off a response, not that the peer received the entire body. Large raw requests that ignore an early rejection remain subject to reset. The measured failure frequency, 2 of 5 for that diagnostic, is not a population estimate or guarantee.

## Regression and integrity

The instrumented HTTP matrix, 13 Phase 7/8 replay cases, five other operational and writer-admission regressions, and the Phase 9 ownership case passed. The ownership digest matched the saved digest and replay produced zero duplicate logical openings, zero hypothetical duplicates, and zero actual notifications. All 269 Python tests passed with one existing dependency deprecation warning. Edge dashboard, all four ULTRON self-checks, Next.js production build, TypeScript, and Git whitespace checks passed.

All 583 historical hashes, 39 frozen Phase 8 benchmark output hashes, and 13 saved Phase 9 result hashes verified. Historical superseded files were verified against their frozen snapshots. No frozen evidence was regenerated or replaced. The historical duplicate-notice blocker and SQLite contention caveat remain open; this transport validation is not deployment or notification approval.

## Files and reproduction

- `scripts/phase9h-http.test.cjs`: five independent runs with the original transport probe, the corrected raw contract probe, actual client verification, and recovery checks.
- `scripts/phase9h-observed-listener.py`: fixture-only observation of the unchanged authenticated Phase 6 application and launcher.
- `scripts/phase9h-evidence.py`: exports the sanitized tables, original failure reference, gate results, and checksums into a new directory; with no arguments it verifies frozen references.
- `docs/phase9h-reference/`: predeclared configuration and results. This report summarizes the evidence.

Set `ULTRON_PHASE8B_PYTHON` to the project test interpreter and `PYTHONPATH` to the absolute `ml/src` path. Run `node --test --test-reporter=tap scripts/phase9h-http.test.cjs` and save its output as `phase9h.tap` in a **new** local evidence directory. To regenerate only Phase 9-H evidence, use `python scripts/phase9h-evidence.py <local-evidence-dir> <new-output-dir> <regression-status-json>` with the archived original TAP copied as `original-regressions.tap` and the boolean regression statuses recorded in `acceptance_gates.json`. Fresh timing and reset frequency may vary; the saved manifest ties this decision to the exact observed run.

No runtime code, contract, policy, deterministic monitoring, or deadlines changed. No commit, push, merge, Phase 10 work, persistent shadow inference, live alerts, or remediation occurred.
