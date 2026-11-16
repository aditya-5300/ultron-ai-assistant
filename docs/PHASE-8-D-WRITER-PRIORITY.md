# ULTRON Phase 8 D Writer Priority

**Recommendation: GO TO PHASE 9 MLOPS LIFECYCLE**, for separately approved research only. All five predeclared contention repetitions passed with cooperative admission, with no lost deterministic samples or critical findings. This is not strict SQLite writer priority, a production latency guarantee, or authorization to enable shadow inference or notifications.

## Baseline and collision audit

The Windows collector measures cadence from the start of each poll. After PowerShell acquisition it analyzes measured history, calls the existing `saveSample` immediate transaction, updates last-sample state and heartbeat, and submits shadow inference asynchronously. An overrun or fresh-sample request can shorten the idle interval. Before Phase 8-D the separate shadow worker had no knowledge of that interval; validated Python output could reach SQLite during the next poll. WAL permits overlapping readers, but never two writers. The periodic heartbeat and writes from other processes are not part of the admission signal.

Phase 8-B's **30.82 ms FAIL** and **20.33 ms PASS** and all five Phase 8-C results (four failures) remain frozen. Phase 8-C reduced the median lock hold for a transition-heavy repeat from 8.21 ms to 1.21 ms, but the unchanged contention workload uses empty batches and injected holds. Reducing serialization alone could not establish deterministic write priority.

The isolated benchmark instruments immediate transaction entry (wait), successful acquisition through commit or rollback (hold), time outside the transaction, requested versus observed injected sleep, and due-to-start lateness. Elapsed timings use a monotonic clock; the original due-time calculation is unchanged. They cannot separate OS scheduling delay from timer wake-up or database I/O precisely. Across paired runs, deterministic BEGIN-wait p95 was **30.74–32.11 ms** without coordination and **15.90–16.10 ms** with admission. Shadow hold p95 in the admitted runs was **15.70–16.71 ms**; observed injected-sleep p95 was **14.80–15.52 ms** despite the smaller requested 5 ms holds on most injected attempts. The reduced lock wait, not universally shorter transactions, explains the measured improvement.

## Admission design and durability

The opt-in collector marks a process-private shared activity flag at poll start and clears it after deterministic work, including error or shutdown exits. The deterministic path only publishes a flag; it never waits on the coordinator or Python. The optional worker defers migration writes and validated replay persistence during that activity, rechecking at 4 ms timer intervals for up to 250 ms. These are scheduling targets, not hard wall-clock deadlines under load. If no idle window appears, the job returns `WRITER_DEFERRED` as a transient failure. A degraded write is best-effort only when activity is clear. Failure recording cannot take priority over the collector; stale status is not proof of system health.

There is one active job and no pending write queue. The existing five-minute spacing and transient backoff (up to 30 minutes) remain unchanged. On exhaustion, the next permitted trigger exports a fresh bounded replay from the last durable checkpoint. Sustained activity can still withhold shadow freshness; recovery requires an idle window. A process restart creates a new signal and reconciles against SQLite, with no persisted process lock to clear.

The check and SQLite acquisition are not atomic; a deterministic poll may begin after the check or while shadow already holds the writer lock. A separate adversarial fixture intentionally showed **50.21 ms** of deterministic acquisition wait in this case, without loss. Admission does not promise preemption. Forecast transitions, consumed identities, eligibility, replay checkpoint and successful run state still use the unchanged Phase 8-C atomic store. No schema, timeout, deterministic policy, API contract or UI design changed.

Fixed next-poll reservations were not selected: PowerShell duration is variable and the fixture's 20 ms injected hold cannot fit between 12 ms deterministic arrivals. Strict priority would require a separately approved single writer or another architectural boundary, with additional coordination and durability costs. Neither was introduced.

## Five paired acceptance repetitions

Each run used 100 critical disk samples per mode, the existing deterministic analyzer and persistence, independent writer scheduling, WAL and separate connections. The shadow worker retained 100 attempts at the original 9 ms cadence with the original 5/20 ms injected holds; only Phase 8-D consulted the activity flag. Pair order alternated as declared by the harness. Admission may delay actual start times; no attempts or stress holds were removed.

| Run | Disabled p95 | Phase 8-C p95 | Phase 8-D p95 | Added p95 | Phase 8-D max | Gate |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 1 | 2.02 ms | 32.60 ms | 18.31 ms | 16.29 ms | 48.73 ms | PASS |
| 2 | 2.12 ms | 35.68 ms | 20.71 ms | 18.59 ms | 48.98 ms | PASS |
| 3 | 2.29 ms | 33.04 ms | 17.55 ms | 15.26 ms | 48.17 ms | PASS |
| 4 | 4.79 ms | 36.01 ms | 18.69 ms | 13.90 ms | 48.98 ms | PASS |
| 5 | 5.81 ms | 33.59 ms | 20.01 ms | 14.20 ms | 49.19 ms | PASS |

All Phase 8-C comparator runs exceeded 25 ms added p95 in this series. All Phase 8-D runs passed. Phase 8-D committed **500/500 shadow writes**, including **107 briefly deferred attempts**, with zero admission expiries, SQLite busy errors, or shadow failures. All 500 deterministic samples and critical observations per mode survived and SQLite quick checks passed. Candidate maximum poll overrun was 50 ms; the uncoordinated comparator reached 1,636 ms in one run. The corresponding worst observed uncoordinated persistence was 1,642.91 ms; no causal attribution of the outlier is asserted. Every paired result, including uncoordinated failures, remains in the CSV.

## Recovery and release limits

The stale-activity fixture reached the admission limit while deterministic critical data remained writable. With a real authenticated, validated replay, deferral left the restart checkpoint unchanged, did not publish false success, and reconciled on the next admitted replay. The repeated response created no new logical or hypothetical opening. Separate tests cover repeated SQLITE_BUSY and in-transaction rollback. The real Windows collector passed all six interruption points, including outstanding inference and pre-commit termination, and recovered at maximum backoff after anchor pruning without restarting. Fault hooks existed only in disposable test copies.

The accelerated replay validated 1,116 transitions in 30 responses, ignored 756 repeats, persisted 360 transitions and exercised five rebases. Six openings were hypothetically eligible and no duplicates or actual notifications were produced. Exact identities, transitions and eligibility also matched across independent fixture databases and a Python restart. The Phase 6 auth, local-only binding and request limits remain unchanged, as do the read-only dashboard, detection scope, process-only forecast policy and `phase8-offline-v1`. No private Windows telemetry, tokens or production database were used in published evidence.

The historical 12 duplicate process notices remain a release blocker (**1.48/device-day** versus **0.5**). Neither the controlled p95 result nor the replay result establishes readiness for production activation. Future work should track tail latency, deferrals and evidence freshness before reconsidering alerts.

## Validation and frozen references

All 26 Node cases passed across the final targeted runs, including the five Phase 8-B operational cases. The first parallel operational run had a collector refusal timeout and an HTTP reset. In serial, the HTTP check passed, while the legacy collector fixture timed out waiting for one replay; the same assertions passed in an isolated diagnostic rerun. The newer six-point collector suite passed without a rerun. These are recorded separately from the five accepted contention repetitions, which were not replaced. The 234 Python tests passed with one existing dependency warning. The Edge dashboard fixture, ULTRON self-checks, Next.js production build, TypeScript and Git whitespace checks passed.

All 583 entries across the historical manifests verified, including 514 original research entries, 29 Phase 8-A entries, 19 Phase 8-B entries and 21 newly frozen Phase 8-C entries. Where current runtime source evolved, the old hashes were checked against exact byte snapshots. The frozen comparisons and decision evidence were not overwritten. Phase 8-D added the admission module, collector and shadow-worker wiring, test timing probes, the evidence exporter, fixtures and separate results.

## Reproduction

On Windows with Node 24, set `ULTRON_PHASE8B_PYTHON` to the test interpreter. Capture TAP from `node --test --test-reporter=tap scripts/phase8d.test.cjs` as the benchmark log, and use the separate Phase 8-C collector and Phase 8-D admission, Phase 8-B operational and Phase 7/8 replay suites for recovery and regression. The structured evidence exporter consumes logs named `ultron-8d-{benchmark,collector,residual,operational,replay}.tap`. Do not run other suites alongside the latency benchmark. The exporter does not overwrite an existing results directory.

No commit, push, merge, Phase 9 branch, persistent inference activation, live ML notifications or automatic remediation occurred.
