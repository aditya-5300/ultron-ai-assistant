# Phase 1: Monitoring architecture discovery

Inspected: 2026-11-10. Baseline: `777a2260f67c2a933529d663cba7b5de0c47f0b4`.
Branch: `feature/phase-1-architecture-discovery`, based on approved `prod`.
Scope: code inspection, isolated synthetic checks, existing checks/build, and this report only. No ML implementation, dependency installation, database migration, or runtime change.

## A. Existing monitoring flow

```text
MonitoringDashboard -> POST /api/monitor (start/config/stop/resample)
                                  |
                         SQLite settings
                                  |
Detached Node health worker <------+  (polls settings; writes heartbeat)
    -> OS counters + Windows PowerShell collector
    -> analyze(current sample, store.modelHistory())
         deterministic rules + median/MAD + gated regressions
         synthetic KB + curated evidence -> structured issues/risks
    -> saveSample() transaction
         samples + issue lifecycle + events + retention
    -> SQLite <- POST /api/monitor (state/history/advice/report)
                       -> MonitoringDashboard (/monitor)
```

**Important difference from the conceptual diagram:** current analysis happens **before** the current sample is persisted. Historical samples come from SQLite; a sample and its rule analysis are saved together. An analysis/save failure can therefore prevent that poll from being persisted. Do not put Python or ML availability into this critical path.

### Scheduling, lifecycle and communication

- Default collection interval is 30 seconds, configurable to 15/30/60/120/300 seconds. The worker measures from iteration start, checks settings approximately every second while waiting, and never overlaps its own collection calls. Queries that exceed the interval delay/overrun the schedule; this is not guaranteed exact 30-second sampling.
- Deep collection runs on the first collection and when at least 300,000 ms have elapsed since the last deep **attempt**. Failed attempts also start this five-minute delay. Successful deep results are reused while `deepTime` is less than 660,000 ms old; reused values are not new sensor observations.
- Node invokes Windows PowerShell with a 45-second timeout, 2 MiB output limit and hidden window. OS CPU/RAM can still be returned when Windows-specific queries fail.
- API start detaches `scripts/health-worker.cjs --background`. `npm run monitor` is the foreground alternative. Collection survives browser/Next.js closure, but not sign-out/shutdown. There is no installed Windows service/autostart or fatal-error auto-respawn.
- Worker and server communicate through SQLite settings, not HTTP, sockets or IPC messages: `config`, `worker`, `resample`, and `lastSample`. A lock file containing PID/nonce prevents duplicate collectors. Heartbeats are written every ten seconds.
- Stop changes `config.enabled`; an in-flight Windows query may finish first. SIGINT/SIGTERM request an orderly stop. Cleanup removes only the matching nonce lock.
- API considers the worker alive using PID existence, heartbeat age <=45 seconds, and a non-error/non-stopped status. Whole-sample staleness is separate: no sample, or age > `max(90 seconds, intervalSeconds * 3)`. Deep sensor staleness is separate again.

## B. Relevant files and responsibilities

| File | Actual responsibility |
| --- | --- |
| `scripts/health-worker.cjs` | Single collector loop, configured cadence, lifecycle/lock/heartbeat, analysis then persistence. |
| `lib/health/collector.cjs` | CPU deltas, physical RAM, per-process deltas/selection, Windows invocation, five-minute deep scheduling/cache, nullable sensor payload. |
| `scripts/collect-health.ps1` | Processes, battery/AC, active power plan; deep fixed-volume space, physical-disk reliability, selected event metadata, numeric battery capacities/cycles. |
| `lib/health/core.cjs` | SQLite store/schema/retention, synthetic KB, rules, median/MAD, regression, forward/backward guidance, issue lifecycle. |
| `lib/health/core.d.cts` | TypeScript declarations for the CommonJS core and store methods. |
| `lib/health/types.ts` | Samples, analyses, issues, risks, history/events, worker/config/dashboard state types. |
| `lib/health/evidence.cjs` | Curated source evidence enrichment for synthetic KB articles; not model training. |
| `app/api/monitor/route.ts` | Local protected POST action API; state/config/lifecycle, approvals, advisory AI, feedback, report and deletion. |
| `lib/health/actions.ts` | Fixed, permission-gated remediation/controller boundary, including verified power-plan change/restore. |
| `lib/controlSecurity.ts` | Local request checks, bounded body/rate handling and action-bound approval tokens. |
| `lib/agentProvider.ts` | Existing Gemini/OpenAI completion integration used for optional health advice. |
| `components/MonitoringDashboard.tsx` | Health charts, coverage, issues, causes, risks, timeline, controls, KB and AI approval preview. |
| `components/MonitoringDashboard.module.css` | Monitoring presentation. |
| `app/monitor/page.tsx` | Dedicated monitoring page. |
| `instrumentation.ts` | Existing startup hook restores desktop voice, not health monitoring. |
| `docs/SYSTEM-MONITOR.md`, `docs/HEALTH-KNOWLEDGE.md` | Existing monitoring/knowledge documentation, subordinate to current code. |

### Telemetry scope

CPU is aggregate OS CPU-time delta; the first/invalid-gap reading is null. RAM is physical usage, not commit/pagefile/private allocation. Selected processes are the union of the top eight by CPU, top eight by RAM and up to eight nonresponding entries, deduplicated by PID. Process CPU uses PID + start time and elapsed-time deltas, not lifetime CPU time. This is not a complete historical process inventory.

Deep logs contain up to 40 matching records per System/Application log from the last hour: IDs 7/18/19/41/51/55/153/1000/1001/1002 at critical/error/warning levels. Only metadata is retained, not event messages. Battery capacity extraction uses a temporary XML battery report, deleted in `finally`; multiple batteries do not receive the single-pack capacity merge. SMART/reliability fields depend on hardware/permissions. No desktop screenshots, window titles, keystrokes, browser history or command lines are collected by this health collector.

## C. Existing database tables/schema

Private database: `<project>/.ultron/health.sqlite`, with SQLite WAL sidecars while active. Access uses Node's built-in synchronous `node:sqlite`, WAL mode and a 5,000 ms busy timeout. Connections are cached per root within each Node process.

```sql
CREATE TABLE samples(id TEXT PRIMARY KEY, time TEXT NOT NULL, data TEXT NOT NULL);
CREATE INDEX sample_time ON samples(time);
CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE events(id TEXT PRIMARY KEY, time TEXT NOT NULL,
                    kind TEXT NOT NULL, detail TEXT NOT NULL);
CREATE TABLE issues(key TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE analyses(id TEXT PRIMARY KEY, time TEXT NOT NULL, text TEXT NOT NULL);
```

- `samples.data`: entire JSON `HealthSample`, including nested rule `analysis`. Metrics are not separate SQL columns.
- `settings.value`: JSON config, worker status, resample request, last sample, risk keys, feedback and related state.
- `issues.data`: current/persisted issue lifecycle keyed by logical issue identity, not a separate row for every observation.
- `events`: append-style detection, resolution, projection, configuration, collector errors, approvals/actions and feedback timeline. Details are bounded strings.
- `analyses`: optional AI advisory text, not structured detections or trained models.
- **No versioned migration framework exists.** Initialization uses `CREATE ... IF NOT EXISTS`. A fresh isolated store reports `PRAGMA user_version = 0`.

### Timestamps, missingness and history

- Sample/event/advisory and lifecycle dates are UTC ISO strings from JavaScript `toISOString()` (millisecond precision); `samples.time` is indexed TEXT and queries sort/filter lexically. Embedded PowerShell log/process/deep dates use UTC round-trip strings, potentially with finer fractional precision. Heartbeat/resample settings use epoch milliseconds. Normalize/validate timestamps at the future boundary; use actual elapsed time, not nominal cadence.
- Unknown numeric sensors are generally `null`; battery provider failure is null, no battery can be `{present:false}`, and partial capacity/AC fields can be null. Arrays can be empty for either no observations or unavailable/stale sensors: consult `coverage` and `deepTime`. Missing selected processes are not zero-usage processes. There is no comprehensive per-field observation timestamp or versioned telemetry schema.
- `history(hours=24, limit=2000)` returns the newest bounded raw JSON rows in the wall-clock lookback, reversed into chronological order. It has no explicit historical end/as-of bound or pagination cursor.
- `modelHistory()` means **existing diagnostic history**, not an ML dataset: seven-day lookback, all samples from the last two hours plus the latest sample per older hour. It is irregularly sampled.
- `chartHistory(hours=6)` selects the latest row in time buckets targeting roughly 360 points. The state API additionally removes historical process/log arrays and nested issues/risks. Do not train on chart history.
- Successful `saveSample()` prunes by configured 1/3/7-day retention and caps samples at 20,160, events at 2,000, AI texts at 30, and issue records at the newest 200. At 15-second cadence the row cap is only about 3.5 days. Reads expose the latest 100 events and five AI texts. Pruning occurs during sample saves, not continuously while stopped.

### Findings and history representation

`HealthAnalysis` contains `issues`, `risks`, `limitations` and a numeric baseline summary. `HealthIssue` includes identity/KB/title/severity, evidence, qualitative causes, checks, allowed next actions, backward/forward traces, and optional lifecycle fields. `HealthRisk` is a key/title/qualitative confidence/evidence projection. Health history is sample snapshots plus the event timeline, not a normalized feature store or causal graph.

`saveSample()` atomically persists the sample, updates issues, creates detection/projection/resolution events and prunes history. Existing active issues become unverified when the relevant sensor disappears; they do not silently resolve. Resolution requires three sufficiently contiguous supported polls without the signal. Persisted issue JSON also contains recovery counters/timestamps not currently declared in `HealthIssue`.

Private application data is under Git-ignored `.ultron/`, including health SQLite, assistant data, browser profiles and temporary/runtime files. Git-ignore is not encryption or an access-control boundary. Health exports can include process names and event metadata; review before sharing. No existing personal telemetry or credentials were needed for this inspection.

## D. Existing reusable functions and diagnostics

| Function/component | Reuse and limits |
| --- | --- |
| `createCollector()`, `cpuTotals()` | Keep collection and missingness behavior; no parallel ML collector. |
| `createStore()`, `latest()`, `getSample()` | Authoritative persisted samples/config; keep Node ownership. |
| `history()`, `modelHistory()`, `chartHistory()` | Existing consumers remain unchanged; none is an adequate paginated, as-of training/export contract as-is. |
| `saveSample()`, `getIssues()`, `event()`, `events()` | Existing transactional rule lifecycle and bounded timeline; do not force ML into this lifecycle without an adapter. |
| `median()`, `regression()`, `contiguous()` | Exported deterministic statistics and continuity gates. Internal `trend()` and `sensorAvailable()` support diagnostics/recovery. |
| `analyze()` and enriched `KB` | Existing trusted deterministic findings remain authoritative for their own rule results. |
| `approvalFor()`, `remediate()` | Preserve action approvals and bounded controllers; ML/LLMs never execute generated commands. |
| Dashboard charts/issue cards/coverage/timeline | Reuse presentation patterns with explicit ML provenance/freshness, not a replacement UI. |

### Rules, median/MAD and regression

- Rules detect sustained CPU >=85% and RAM >=90% across at least three contiguous observations with duration gates; low disk space (<10%, critical <5%); storage warning/unhealthy/wear/errors/temperature; reduced reported battery capacity (<80%, critical <60%); known high-performance plan on battery; low charge; selected restart/storage/crash events; repeated nonresponding process identity. These are heuristics, not vendor-certified failure predictions.
- CPU baseline compares similar AC-state history, requiring >=60 finite CPU readings for an anomaly. It uses median/MAD, a five-percentage-point MAD floor, scale factor 1.4826, robust deviation >=4 and current CPU >40%, unless the sustained CPU rule already fires. Unknown AC states can match each other.
- Battery drain uses contiguous unplugged observations, >=6 points, >=8 minutes, negative slope and R-squared >=0.6. Threshold is initially 15 percentage points/hour; >=20 historical windows permit a median/MAD adaptive threshold with a conservative floor. Short-term time-to-10% is separately extrapolated under unchanged-load assumptions.
- RAM projection requires >=10 observations over >=10 minutes in a recent 30-minute window, R-squared >=0.8, growth >1 percentage point/hour and current usage between 75% and 95%; projects reaching 95% within two hours.
- Disk-space projection requires >=12 hourly observations spanning >=24 hours, R-squared >=0.7, falling free space and current free space between 10% and 25%; projects reaching 10% within seven days.
- Backward tracing is evidence plus possible-cause strings/qualitative associations. Forward tracing is guidance and gated trend risks. Neither is a learned causal graph or guaranteed hardware-failure forecast.

### AI, API, errors and existing tests

`POST /api/monitor` multiplexes `state`, `start`, `stop`, `config`, `sample`, `remediate`, `feedback`, `ai`, `report` and `clear`. It enforces local/same-origin request controls, bounded input and rate checks. Deletion requires the exact confirmation phrase and a stopped worker. Feedback is stored for review; it does not retrain anything.

Optional `ai` loads a stored sample, previews a reduced evidence payload and requires approval bound to the sample/payload hash. It calls the existing Gemini/OpenAI provider and saves redacted advisory text. Process names, raw logs, screenshots and command lines are excluded from that cloud payload. This is explanation/retrieval assistance, not training or detection authority. Generated text does not call remediation tools.

Sensor-specific PowerShell errors become coverage/nulls; a total query error can still yield CPU/RAM. Worker-loop failures record heartbeat/error events and retry; fatal errors exit. Store writes roll back on failure. API exceptions return error responses. The dashboard polls every 15 seconds only while visible, retains last-known state on a failed request and displays an error; it does not drive collection. Charts split at missing readings/large gaps rather than drawing healthy zeros.

There is **no registered monitoring-specific suite in this repository**. An existing external fixture at `../../outputs/health-selfcheck.cjs` exercises core rules, baseline/trend gates, missing sensors, persistence, issue recovery and deletion guards using synthetic data. Registered command/agent/browser-tab/native-speech self-checks cover adjacent functionality, not end-to-end monitoring. Worker timing, actual PowerShell behavior, monitor API approval flows and dashboard interactions lack equivalent dedicated automated coverage.

## E. Components that should NOT be changed

- Existing worker/collector cadence, Windows read-only queries and SQLite sample-save path.
- Existing rules, median/MAD diagnostics, trend gates and issue recovery semantics.
- Local request security, explicit cloud consent, deletion and remediation approvals.
- Chat/voice/gesture/orb/app/browser/phone controllers, unrelated agent functionality and existing dashboard behavior.
- `main` and `prod` history: this phase changes documentation on its feature branch only.

Later additive instrumentation may be necessary, but must be reviewed in its requested phase; this report is not authorization to refactor these components.

## F. Exact recommended ML integration

```text
UNCHANGED: worker -> collect -> existing analyze -> SQLite transaction
                                                      |
ADDITIVE: Node-owned background ML adapter <-----------+ persisted raw window
    -> bounded, sanitized, as-of telemetry contract
    -> loopback Python feature pipeline -> approved Isolation Forest inference
    <- validated ML result (including insufficient/unavailable outcomes)
    -> Node persists ML result/status

Existing issues + existing trend risks + separate ML findings
    -> provenance-preserving findings aggregator -> additive dashboard fields
```

**Choose backend-mediated telemetry, not Python reading the live SQLite file.** Node remains the sole application database owner. This avoids a second schema/retention/privacy implementation and direct coupling to WAL files or embedded analysis JSON. Python receives no database path or database write access.

The future Node adapter should run independently of page polling and outside the health worker's collect/analyze/save critical path. It observes new persisted samples, requests bounded chronological raw windows ending at a specified sample/time, and calls Python with deadlines and bounded catch-up/concurrency. Python failure must not delay collection or prevent existing API/dashboard results. When Next.js is down, the independent existing collector still runs; this proposed server-owned ML adapter pauses. An always-on independent ML supervisor is not implicitly promised.

Add an explicit history/export function with start/end bounds, stable `(time,id)` cursor/order and limits. Do not expose the unrestricted DB handle to Python, reuse chart data, or include existing rule labels/AI text/feedback as model inputs. Send numeric metrics, required power/context fields, nullable values, coverage and deep observation age only. Feature engineering must distinguish cached deep readings, missingness and sampling gaps.

Training is an explicit offline command using a versioned, privacy-reviewed export/manifest, not a live API action. Split chronologically before fitting preprocessors, use past-only rolling windows, evaluate on held-out later data and gate deployment. Feedback is not ground truth and must not silently trigger retraining. Isolation Forest identifies unusual patterns; existing/separate early-warning logic estimates trends. LLM explanations may later consume validated findings but cannot decide anomalies or execute actions.

## G. Database changes likely required

**Phase 1: none. An initial Python/contract foundation need not change SQLite either.** When persistence/integration is actually requested:

1. Add a small Node-owned, versioned migration mechanism before modifying existing databases; retain compatibility and test upgrades/rollback/recovery expectations.
2. Prefer an additive `ml_results` table for scored windows, including normal/insufficient-data outcomes: result ID, anchor sample ID/window bounds, schema/model versions, window/input fingerprint, observation end and creation timestamps, status and validated structured JSON. Index by window end and enforce an appropriate model/schema/window deduplication key.
3. Keep ML enablement/cursor/last-error state in namespaced settings or a small dedicated status table. Define retention/deletion alongside existing private-history controls. ML unavailable/stale/disabled is not a healthy score.
4. Do not rewrite historical sample JSON, change existing issue keys, or fabricate a synthetic KB ID for every ML anomaly. Existing `HealthIssue` provides useful display concepts but lacks source, score, threshold, model/schema version and input-window provenance. Introduce a runtime-validated versioned ML result/finding contract and an additive display adapter.
5. Store immutable model artifacts/manifests/checksums privately outside telemetry tables. Version feature schemas/training configuration in Git; version model artifacts separately with evaluation/promotion metadata. MLflow is deferred until its requested phase.

Exact SQL, result contract and retention sizes remain future-phase design work, not implemented schemas.

## H. Risks and design assumptions that do not match code

1. **Analysis order:** rules/MAD/trends run before current-sample storage, not downstream of it. Preserve this and add asynchronous ML after persistence.
2. **Sampling:** configurable/overrunning cadence, repeated deep cache values, selected-process bias and mixed-resolution history can create false patterns. Actual observation times and quality gates are essential.
3. **Temporal leakage:** `analyze()` filters future rows for its main sequence, but CPU median/MAD uses the original history argument without an as-of filter. An isolated future-only-history fixture produced a baseline anomaly. Normal worker history is ordinarily past data, but retrospective evaluation/clock rollback cannot assume this. Do not use this function as an unbounded retrospective evaluator; a separately approved fix/test is advisable.
4. **Timestamp stability:** wall-clock cutoffs/lexical ordering assume valid comparable UTC strings; clock changes and equal timestamps need explicit handling. No immutable device/boot/feature schema identity currently exists.
5. **Evidence limits:** seven-day/row-capped retention, no confirmed failure labels and no representative evaluation corpus cannot establish predictive accuracy. Synthetic KB/fixtures are not learned ground truth or hardware-failure validation.
6. **Missingness:** empty arrays are ambiguous without coverage. Disk DeviceId is not a durable hardware replacement identity; battery percentage is quantized and multiple-pack fields are limited. Generic sensor thresholds are not universal hardware specifications.
7. **Isolation:** synchronous SQLite and a five-second busy timeout can block Node work. Keep ML writes bounded and away from collector transactions; do not add a Python SQLite writer.
8. **Lifecycle/errors:** PID-only liveness has PID-reuse limits; corrupt locks fail closed. A ten-second heartbeat can overwrite a transient loop error status. Sample freshness and sensor coverage remain necessary even when a PID is alive. Fatal worker errors are not automatically restarted.
9. **Privacy/security:** local storage is not encrypted. Existing state/report can contain more detail than a future ML contract needs. Do not weaken the same-origin guard so Python can call browser control routes; Python must not receive action authority.
10. **Types/UI:** TypeScript interfaces are not runtime JSON validation. Current issues are KB/rule-oriented, and stale last-known UI state can survive API failures. ML needs independent provenance/freshness/status, not a replacement for existing `stale`.
11. **Test gaps:** build/core fixtures are not live sensor validation or API/UI integration coverage. No actual learned model, evaluation gate, Python service or model registry currently exists.

## I. Proposed new ML folder structure (not created)

```text
ml/
  pyproject.toml
  configs/                         # versioned training/inference settings
  schemas/                         # versioned boundary/feature schemas
  src/ultron_health_ml/
    contracts/                     # Pydantic request/result validation
    features/                      # one shared train/infer feature pipeline
    training/                      # offline export input, split, fit, evaluate
    inference/                     # FastAPI, approved artifact loading, scoring
    early_warning/                 # separate later work; do not duplicate rules
  tests/                           # pytest contracts/features/leakage/failure tests

lib/health/mlBridge.ts              # later Node scheduler/client, no collection
lib/health/mlHistory.ts             # bounded sanitized history/export adapter
lib/health/findings.ts              # explicit source-aware aggregation
.ultron/ml/artifacts/<version>/     # private immutable artifacts; not Git files
```

Python dependencies, when requested: NumPy, Pandas, scikit-learn, FastAPI, Pydantic, joblib and pytest. No Kubernetes/Kafka/Spark/Airflow or early MLflow infrastructure.

## J. Proposed Next.js / Python boundary

- Bind FastAPI to loopback only, use a per-installation secret for internal requests and validate sizes, bounds and contract versions. Do not enable general browser CORS or put that secret in client bundles. Loopback alone is not authentication.
- `GET /healthz`: service readiness, approved model/schema versions; no personal telemetry. Versioned `POST /v1/infer`: validated window + nullable numeric/context/quality fields, correlation/sample/window IDs and as-of time. No live training endpoint.
- Response: data-quality status, observation bounds, source/model/feature-schema versions, declared score direction, threshold, anomaly decision and evidence. Insufficient data produces no invented healthy values. Validate replies again in Node; reject incompatible versions/artifacts and stale results.
- Use bounded requests, timeouts, capped retries/backoff and an explicit degraded state. Persist accepted outcomes and expose cached additive ML state through the existing backend; never make normal dashboard state wait for Python.
- Training reuses the same Python feature implementation but runs separately. Promotion requires explicit evaluation approval; loading an artifact is not permission to train or deploy a new one.
- Existing approval-gated controllers exclusively own actions. An inference response, recommendation or AI paragraph is data, never an executable command.

### Files likely to change in Phase 2+ (not authorization to start)

New `ml/` contracts/features/service/tests and later Node adapters above. Additive changes may be needed in `lib/health/core.cjs` / `core.d.cts` for bounded reads, migrations and ML persistence; `types.ts` for optional ML state; `app/api/monitor/route.ts` for additive cached results; dashboard/CSS for ML display; and `instrumentation.ts` for server adapter initialization. Add dedicated tracked monitoring/ML tests and documentation. Windows collector/worker, deterministic diagnoses and unrelated assistant functionality should remain unchanged unless a separately scoped compatibility need is approved.

### Phase 1 verification and stopping point

Inspection verified schema using a fresh temporary database, not the user's health history. A synthetic replay probe confirmed the CPU baseline as-of risk above.

| Validation run | Result |
| --- | --- |
| `node ../../outputs/health-selfcheck.cjs` | Passed synthetic rules/trends/baseline, missingness, persistence, recovery and deletion fixtures. This script is outside the tracked repository. |
| `npm.cmd run check:commands` | Passed command-routing and security fixtures. |
| `npm.cmd run check:agent` | Passed agent permissions, evidence, recovery and dispatch checks. |
| `npm.cmd run check:browser-tabs` | Passed owned-tab/command fixtures; no real browser launched. |
| `npm.cmd run check:native-speech` | Passed speech-engine/generated-audio routing checks; no live microphone test. |
| `npm.cmd run build` | Passed production compilation, TypeScript checking and page generation. |
| `git diff --check` | Passed for tracked changes; the only new repository file is this report. |

These checks do not prove live Windows sensor correctness, failure prediction or model accuracy. The validation log is outside the repository at `../../outputs/phase1-validation.log`. No tracked runtime file changed; `main`, `prod` and this branch's HEAD still reference the inspected baseline.

Deliverable: this architecture report only. No ML service/model, schema migration or runtime changes were made. Future phases require explicit instruction; commit/push and integration require their separate approvals.
