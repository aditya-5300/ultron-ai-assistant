# ULTRON Phase 8 Shadow Observability

**Decision: REPEAT PHASE 8.** The read-only dashboard and controlled eligibility evaluator work, and replay produced no duplicate hypothetical openings. The acceptance gate is not complete: restart coverage does not include the full Windows collector process, SQLite tests do not exercise independently timed competing writers, and extended outage recovery has not been validated under the actual collector lifecycle.

## Architecture and access

The existing collector, deterministic analysis, production monitor API and SQLite deterministic tables are unchanged. Only the optional shadow worker uses the Phase 6 service; Python never opens production SQLite. The existing worker still requires explicit `ULTRON_INFERENCE_MODE=shadow`; absent that setting, no inference scheduler starts. No browser action enables inference or changes policies. No persistent shadow mode was activated.

The monitor links to `/monitor/shadow`. The page uses the system monitor's visual language and separately labels experimental inference, with a link back to deterministic monitoring. It displays status, handshake compatibility, last attempts, latency, the replay checkpoint, coverage, advisory findings and an ordered lifecycle. Active means active **in replay**, not a confirmed current incident. Legacy openings are marked as unrecorded, not counted as new. Coverage transitions, cached/missing readings, stale status, and absent history are distinguishable from a healthy verdict. No uncalibrated score is rendered as probability.

`GET /api/monitor/shadow?view=status|metrics|findings|transitions` uses a SQLite read-only connection with a 20 ms busy limit and one snapshot per request. Results are not cached. Findings and transitions allow 1–50 rows per page, at offsets 0–1000; a validated finding hash selects an ascending lifecycle. Other parameters are rejected. Requests must have a loopback Host, same-origin browser Fetch Metadata and the explicit application read header. There is no CORS or mutation handler. This follows the application's local trust model; it is not authentication against a malicious native process or a reverse proxy exposing the app. Run ULTRON locally, not behind a public proxy. The API does not return raw telemetry, paths, process names, installation tokens, evidence scores, or internal exceptions.

## Schema and measurement

The version 2 migration transaction adds `shadow_runs`, `shadow_eligibility`, `shadow_rebase_guard`, and read indexes. It preserves v1 records and the original deterministic schema. Migration only runs on a shadow job, not on a dashboard request. History clearing includes the new shadow tables. Version 0/1 reads do not create or upgrade a database. Run data is bounded to 500 records; eligibility follows replay retention. Recorded counts are not lifetime counters. A locked database can prevent a failure from being logged. Scheduler counts are snapshots from dispatch, reset at scheduler restart, and cannot establish the latest circuit state between jobs.

Successful storage records raw transitions, verified transitions, repeated events, durable writes, eligible openings and per-stage timing in the same transaction as the checkpoint. Bootstrap and rebased replay can create historical rows; these rows are not live notifications. Failure reporting retains allowlisted codes only. Missing instrumentation is shown as unavailable.

## Offline eligibility policy

`phase8-offline-v1` considers only a fresh OPEN beyond the previously committed replay watermark. A unique owner and event ledger prevent repeat counting; stale responses and inconsistent history fail before counting. UPDATE, forecast-to-detection ESCALATE and CLOSE do not qualify. There is no time-based cooldown: a confirmed fresh recovery can permit a genuine new episode without suppressing other subsystems. Bootstrap and rebase openings are historical. If rebase interrupts an active owner and the new replay cannot prove continuity, later openings in that subsystem are conservatively withheld until fresh closure; this can withhold a genuine new event and is a release limitation. Deterministic critical alerts are outside this evaluator. There is no delivery or remediation code.

CPU/workload and disk capacity remain detection only. Process responsiveness is the sole forecasting scope, with `persistence:1800:0.65` and 240 seconds minimum useful lead. This is not a prediction of app crashes or hardware failure.

## Evidence and limitations

- **Store-level replay:** Three simulated days of input, 71 overlapping replay steps, six rebases and eight connection reopenings. 452 offered transitions, 410 repeats, 42 persisted transitions, 21 eligible synthetic episodes (20 recurrences), and zero duplicate openings. This run tests the storage lifecycle, not Python validation.
- **Real loopback replay:** 30 requests of 181–1,261 rows, 1,116 raw and validated transitions, 756 repeated transitions, 360 persisted transitions, five rebases and six simulated eligible openings. Repeated responses and rebases added no hypothetical interruptions. The original restart fixture separately restarts Python and the Node worker and checks forecast → detection → closure for a single owner. No notifications were sent in either run.
- **Replay limits:** Tested history rotation, the 1,800-row boundary, retention with insufficient warmup, a sleep gap, superseded ownership and ambiguity withholding. Cached deep and missing evidence are covered by the existing export fixture. The body cap remains 2 MiB; the row-limit fixture checks its encoded size, not near-cap end-to-end traffic.
- **Failure scenarios:** Fixtures cover unavailability, auth rejection, compatibility mismatch, slow and partial HTTP, stale/late consumption, bounded scheduler backoff and recovery, and transactional migration rollback. They do not prove a full collector restart or multi-day outage/reconnection with the real scheduler.
- **Isolation:** Two equivalent 40-sample runs retained every deterministic sample and critical finding. Eight forced shadow lock conflicts were rejected with bounded waits. The measured added deterministic p95 was about 0.50 ms versus the predeclared 25 ms fixture threshold. The lock fixture is interleaved rather than a representative concurrent-writer benchmark. It does not rule out deterministic delays from SQLite writer contention.
- **Latency:** The generated CSV reports median and p95 where at least 20 samples exist, including export, encoding, HTTP, validation, shadow persistence and in-process read handler. Small and large groups are separate. It does not measure Windows acquisition, independent writer contention, or end-user browser latency, and is not an SLA.
- **Verification:** All 234 Python tests, the 13-test combined Node replay suite, the production-build Edge dashboard fixture, ULTRON health/command/agent/browser-tab/native-speech self-checks, Next.js build, TypeScript, and whitespace checks were run. The dashboard fixture covers loading, disabled, unavailable, degraded, historical/active/closed, lifecycle, paging, advisory labeling, no mutation controls and error rendering. Native speech uses synthesized fixtures, not a live microphone.

## Reproduce the controlled evidence

Use the existing Python environment with `PYTHONPATH=ml/src`. Run `python scripts/phase7-e2e.py --phase8`, capture its TAP output, then run `python scripts/phase8-report.py <tap-file> <new-output-directory>`. For the browser fixture, build the app, set `ULTRON_PHASE8_TEST_BROWSER` to a local Edge or Chromium executable, and run `node --test scripts/phase8-browser.test.cjs`. The fixture uses a temporary database and a separate loopback listener.

`docs/phase8-reference/` contains the predeclared configuration, observability contract, acceptance gates, structured results and reproducibility manifest. The digest excludes timings, absolute dates and ephemeral IDs. All 514 frozen research hashes matched; no Phase 4/4.5/5 evidence was regenerated and the Phase 6 contract and frozen policies are unchanged.

## Files and next gate

Added the observability route, dashboard and styles, read-only data access and offline eligibility modules, Phase 8 Node and browser suites, evidence exporter, and Phase 8 reports. Updated the monitor navigation, optional shadow client/job/thread/scheduler/store and the temporary Phase 7 harness entry point. No commit, push, merge or Phase 9 branch was created.

Before recommending Phase 9, exercise the complete Windows monitoring-worker lifecycle under isolated restart/outage and realistic concurrent SQLite writes. Record recovery time and collector latency, and measure boundary behavior near the body cap. The 12 historical duplicate process notices still block user-facing release: counting them gives approximately **1.48 interruptions/device-day** against **0.5**. The controlled runs do not resolve that blocker or establish live delivery safety.
