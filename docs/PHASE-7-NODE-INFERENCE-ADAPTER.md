# ULTRON Phase 7 Shadow Inference Adapter

**GO TO PHASE 8 SHADOW OBSERVABILITY** is a recommendation for separately approved work, not an alerting or remediation release. The adapter is disabled by default. Only temporary databases and a temporary loopback service were used for the end-to-end run. No persistent shadow mode was enabled on the user's installation.

## Data flow and authority

The health worker runs its existing deterministic analysis, commits the sample and issues in its existing transaction, and updates its heartbeat. Only then may it submit the committed sample ID to a separate Node worker thread. The worker opens a separate connection to the same database and performs export, HTTP, validation and shadow persistence. No Python request or response is awaited by the collection loop. Python has no database access and no operating-system authority. Shadow findings never enter the deterministic issue or notification paths. A Python critical finding is advisory reference evidence only, not a replacement for Node's critical monitoring.

## Setup and secure compatibility

For controlled testing, use the Phase 6 standalone launcher with its private `ULTRON_INFERENCE_TOKEN`. In the monitoring worker's private environment, use the same token, set `ULTRON_INFERENCE_MODE=shadow`, and optionally set `ULTRON_INFERENCE_PORT` (default 8765). Unset the mode to disable. Do not expose this token in browser code, configuration committed to Git, request logs, SQLite, or reports. There is no browser control or alerting mode.

The Node client uses native HTTP to numeric IPv4 loopback only; no arbitrary URL, proxy, or redirect is supported. It authenticates metadata and inference and checks all schema/API versions, both feature fingerprints, the frozen policy version, exact supported scopes and declared size limits before sending telemetry. Connection and request deadlines are 1.2 s and 15 s per call. Metadata is capped at 8 KiB and the inference response at 5 MiB. Metadata mismatch blocks inference. Response validation checks field allowlists, request/replay IDs, state and transition progression, evidence against the submitted readings, bounded counts, and IDs derived from the replay, event, and sample. CPU and disk remain detection only. Process alone forecasts with the unchanged `persistence:1800:0.65` policy and a 240-second useful lead.

## Telemetry export and replay policy

A dedicated query reads committed samples ending at the trigger ID, not dashboard/chart/model history. It validates canonical UTC timestamps and sample IDs in indexed time order and derives elapsed intervals from adjacent acquisitions. It sends only aggregate CPU, RAM context, the fixed C: volume's free percentage, selected-process nonresponse count, AC state and a coarse power-plan enum. No process names, drive labels, event logs, files, secrets, or original sample objects cross the boundary. Missing values remain null; an absent C: volume is not replaced by another drive. Disk readings use the original deep observation timestamp, reducing Windows' seventh fractional digit to the Phase 2 contract's microsecond precision. Cached readings are not independent samples.

Replay starts at a persisted anchor with at least 90 minutes of history; the service's first hour is advisory warmup. A gap over ten minutes starts a new continuity segment. The anchor is reused until it disappears or the 12-hour/1,800-record limits require a rebase. A body over 2 MiB is rejected, never silently shortened. Retention, warmup and invalid history produce separate shadow status, not a healthy verdict. Normal sample gaps inside the segment are preserved for the Phase 6 engines. Replays assume a single selected volume and a single monitoring installation.

## Durable storage and ownership

When shadow mode first runs, an additive transaction upgrades `PRAGMA user_version` from 0 to 1. It creates `shadow_status`, `shadow_attempts`, `shadow_checkpoint`, `shadow_findings`, `shadow_transitions`, and `shadow_consumed` with indexes and unique identities. Failed migration rolls back; existing tables and data are not rewritten. The existing stop-before-clear operation includes the shadow tables and renews installation identity. Only bounded, validated finding evidence and provenance are stored, not replay requests or raw telemetry copies.

Each installation and anchor determines a replay namespace. The checkpoint stores namespace, anchor, as-of, policy, and last consumed event ID. Within a namespace, event IDs and canonical evidence digests detect repeats and conflicts. The same history must contain the previously consumed prefix; altered or incomplete prefixes and stale responses fail closed. Findings, transitions, consumed identities, cursor and successful status are committed atomically. Forecast OPEN, UPDATE, detection ESCALATE and CLOSE share a logical owner; escalation never counts as a second opening. Genuine recurrence after fresh recovery can open a new owner. A rebase supersedes old active findings and imports the replacement replay as historical context without counting its OPENs as new. Bootstrap records historical openings but treats them as ineligible for hypothetical interruption. Consumed IDs from the old namespace are discarded; inactive namespace history is pruned after seven days and attempts are capped at 500.

## Backpressure and failure behavior

Only one job is admitted; triggers while busy or before the next five-minute slot are discarded in favor of a later committed sample. There is no pending queue. Transient failures open a backoff circuit after three failures, capped at 30 minutes, with the next permitted trigger acting as a probe. Malformed requests and incompatible replies are not retried in a loop; later samples can be checked at the normal interval. The thread uses a 50 ms SQLite busy wait and sanitizes failures into shadow status where the database is writable. A locked/unwritable database can prevent a status update; the collector's deterministic path does not depend on it. SQLite still has a shared writer lock, so this does not guarantee zero contention in a live installation.

## Controlled validation and performance

Run `node --test scripts/phase7-shadow.test.cjs` for the Node fixtures, or run the full restart harness from the project root with the existing ML environment:

```powershell
$env:PYTHONPATH = 'ml/src'
python scripts/phase7-e2e.py
```

The harness uses ephemeral credentials, temporary SQLite files and the real Phase 6 loopback launcher. It restarts the Python listener and Node worker thread, replays an overlapping committed prefix, then repeats it. Across three responses it received **129 raw transitions**, persisted **one process owner** with forecast OPEN → detection ESCALATE → CLOSE, and counted **zero duplicate logical openings and zero hypothetical eligible interruptions**. No notification was sent. Separate fixtures verify delayed-response rejection, transaction rollback, migration idempotence, rotation, confirmed recurrence, malformed/partial HTTP responses, authentication and compatibility, timeouts, backoff, cache privacy and SQLite contention.

On the three 181–216-row requests, median / observed max in milliseconds were approximately: export **7.15 / 7.42**, JSON construction **0.31 / 0.32**, HTTP round trip **226 / 247**, validation **2.03 / 2.32**, persistence **2.90 / 3.86**. With only three measurements, the maximum is shown instead of a stable p95. The HTTP timing includes metadata, Python replay and network overhead; it is not an isolated Python timing or a production SLA. The Phase 6 report separately benchmarks Python's service processing on larger fixtures.

All **8 Node tests** (including real-service replay), **234 Python tests**, ULTRON health/command/agent/browser-tab/native-speech self-checks, Next.js production build, TypeScript and Git whitespace checks passed. Native speech used generated audio. All **514 frozen SHA-256 hashes** matched; no historical replay or research artifacts were regenerated.

## Limitations and release gate

The Phase 4.5-C **12 duplicate process notices** remain an open user-facing deployment blocker. Counting them historically raises the rate to about **1.48/device-day**, above 0.5/day. Temporary replay results do not demonstrate suppression across all live window rotations, outage/reconnection histories, or future notification channels. Phase 8 should add internal shadow observability and test longer controlled restart, rebase, and contention scenarios before considering any notification release. No live alerts, automated remediation, production database test writes, or Phase 8 work were performed.

**Files:** Added the `lib/health/shadow/` contract, export, client, migration/store, job, thread and scheduler modules; `scripts/phase7-shadow.test.cjs`; `scripts/phase7-e2e.py`; and this report. Updated the post-commit hook in `scripts/health-worker.cjs` and history clearing in `lib/health/core.cjs`. No change to Phase 6, the deterministic analyzer, existing API routes, dashboard, or controllers. Nothing committed, pushed or merged.
