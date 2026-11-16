# ULTRON Phase 8 C Write Path Reliability

**Decision: REPEAT PHASE 8.** The change shortened the measured SQLite writer-lock hold on transition-heavy repeats. The real Windows collector recovered at six interruption points and after maximum scheduler backoff. However, four of five unchanged concurrent-writer runs exceeded the 25 ms added deterministic p95 limit. The latency gate failed even though no deterministic data was lost. Phase 9 and live notification delivery are not approved.

## Frozen evidence and initial transaction audit

The 514 Phase 4/4.5/5 hashes, 29 Phase 8-A hashes and 19 newly frozen Phase 8-B hashes matched. The one evolved source file, `lib/health/shadow/store.cjs`, has an exact Phase 8-A byte snapshot in `docs/phase8c-reference/phase8a-store.cjs.txt`; the original manifest still verifies that snapshot. The other references remain at their original paths. Phase 8-B still records **30.82 ms FAIL** and **20.33 ms PASS**; neither value was overwritten.

The original shadow success path used `BEGIN IMMEDIATE` before checkpoint and sample-anchor reads, followed by rebase reconciliation, SQLite statement preparation, JSON serialization, timestamp conversion and canonical SHA-256 evidence digests. It then performed per-event checks, transition/finding/eligibility writes, checkpoint advancement, pruning, status/attempt updates and the run summary before COMMIT. Migration uses separate immediate transactions; failure status uses separate best-effort autocommit operations. The stop-before-clear operation is its own transaction. The 50 ms shadow busy timeout and 5,000 ms deterministic busy timeout remain unchanged. A busy database can prevent status persistence; it must not fabricate success.

Phase 8-C moves event preparation before `BEGIN IMMEDIATE`, caches SQL statements per connection, and skips unchanged checkpoint writes. Stale checkpoint and anchor checks, event uniqueness, rebase ownership, eligible openings, pruning and successful run state remain in one transaction. An injected exception after writing the run summary rolled back all seven affected shadow record sets and left the checkpoint unchanged; retry succeeded without duplicate openings. In separate stress, another writer forced three bounded busy errors; the checkpoint did not move until a later successful retry. No new database, schema, queue or busy-timeout extension was introduced.

In a separate in-memory diagnostic with 600 repeated transitions, median lock hold fell from **8.21 ms to 1.21 ms**, and p95 fell from **10.18 ms to 1.50 ms** (30 repeats per implementation). Lock hold was measured after successful BEGIN through COMMIT. The unchanged Phase 8-B benchmark uses empty transition batches and injected 5/20 ms holds; it does not benefit directly from moving per-event hashing. The benchmark shows unresolved shared-writer delay, not proof that hashing caused Phase 8-B's failure or that the optimization worsened production latency.

## Unchanged contention acceptance benchmark

The original 100 critical disk samples per mode, independent writer scheduling, separate WAL connections, shadow cadence, and 5/20 ms holds were retained. All five predetermined results are listed below. The unit test also checks data integrity independently of the acceptance decision.

| Run | Disabled p95 | Shadow p95 | Added p95 | Shadow max | Gate |
| --- | ---: | ---: | ---: | ---: | --- |
| 1 | 3.33 ms | 51.71 ms | 48.38 ms | 79.73 ms | FAIL |
| 2 | 11.80 ms | 49.61 ms | 37.80 ms | 66.77 ms | FAIL |
| 3 | 12.01 ms | 35.39 ms | 23.39 ms | 557.00 ms | PASS |
| 4 | 3.54 ms | 50.31 ms | 46.77 ms | 81.87 ms | FAIL |
| 5 | 12.11 ms | 37.34 ms | 25.23 ms | 65.44 ms | FAIL |

The candidate retained **500/500 deterministic samples and 500/500 critical observations** over the five runs. SQLite quick checks passed; 499 shadow writes committed and one was rejected as busy. The CSV includes polling overruns and does not use a pass average to hide failures. The maximum is an observed outlier; system scheduling, deliberate stress holds and SQLite wait time were not individually attributed. These accelerated measurements are not a production SLA. Combined restart/outage while the independent contention writer is actively blocked was not characterized.

## Windows collector interruptions and extended outage

The harness copies the actual Windows collector and its dependencies into disposable roots, runs the existing read-only PowerShell acquisition and deterministic analyzer, and adds marker-based fault hooks only to copied files. It killed and restarted the collector after sample commit, before dispatch, while the HTTP request was outstanding to Node, after response receipt, during the uncommitted shadow transaction, and after shadow commit. Each deterministic sample survived. Only the post-commit case had a visible shadow commit at termination. All restarts yielded one durable historical owner and no hypothetical opening, and SQLite checks passed. Across the six cases deterministic recovery took **6.78–13.97 s** and shadow recovery **8.11–16.64 s** on this host. No fault hooks ship in the production collector.

In a separate isolated actual-collector run, Python stopped after initial success. The real scheduler, using an accelerated scheduling clock only in the fixture, recorded five unavailable attempts at virtual minutes 5, 10, 15, 25 and 45. Polling continued. After pruning the anchor, the same PID rejected a trigger one virtual millisecond early and recovered at minute 75 with a new namespace and no eligible opening. Wall-clock duration including initial success and final shutdown was about **28.3 s**; no inference outage was interpreted as healthy. The full collector tested actual pruning and rebase, not 12 hours or 1,800 acquisitions inside the collector. Those bounds, sleep gap, and insufficient warmup are covered separately in the replay/export fixtures.

## Ownership, HTTP and validation

The three simulated days of fixture input produced 1,116 raw and validated transitions over 30 requests. Of those, 756 were repeats and 360 were persisted. Five rebases produced no new eligible opening solely from rotation. Six openings were hypothetically eligible in the scripted replay; **zero duplicate logical openings, zero duplicate hypothetical openings, and zero actual notifications** were observed. Existing fixtures exercise forecast-to-detection escalation, closures and genuine recurrence; a retry or replay does not count as a recurrence. Exact identities, transitions and eligibility matched in two independent databases across a Python restart. The digest is `d086e5d9e640e1ed64d86bf5043c4a3ac2f16f46e7584801161bad062a5a297d`.

The original HTTP regression accepted exactly 2 MiB of valid JSON including legal padding, rejected one byte over with HTTP 413, and retained the 1,800-observation and near-12-hour checks. The Phase 6 compatibility contract, auth requirements, fingerprints, analytical policies, loopback boundary, eligibility policy and read-only dashboard/API are unchanged. CPU and disk remain detection-only; process responsiveness is the only forecast, with `persistence:1800:0.65` and 240 seconds minimum useful lead. Temporary test credentials and real host telemetry were not exported. No shadow inference or alerts were activated on the installation, and no remediation was executed.

**Validation:** 22 Node tests (13 replay, five operational, two collector, two write-path), 234 Python tests, the Edge browser test, ULTRON self-checks, Next.js production build, TypeScript and whitespace checks passed. The legacy replay launcher's Python health probe timed out; the same unchanged test suites passed through the new isolated Node launcher. Python emitted one upstream dependency warning. Tests ran on Windows 11 with Node 24.18.0 and Python 3.13.12.

## Files and next gate

Changed only the existing shadow store in runtime code. Added isolated lifecycle and transaction tests, a Windows-safe replay runner, evidence exporter, configuration, the byte-exact store snapshot, frozen hash manifest, transaction audit, structured results and this report. No commit, push, merge or Phase 9 branch was created.

Investigate bounded shadow write admission that avoids collisions with deterministic persistence, then rerun the **same** five-run benchmark. Do not weaken transactional ownership or tune the threshold. The historical 12 duplicate process notices still block user-facing rollout: **1.48 interruptions/device-day** versus **0.5**. Controlled replay does not establish live notification readiness.
