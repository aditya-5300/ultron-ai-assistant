# ULTRON Phase 9-J Writer Architecture Assessment

**Decision: RECOMMEND ARCHITECTURAL CHANGE. Preferred option: C, a separate Node-owned shadow SQLite database.** Seek separate approval to design, prototype and validate the split. Keep current runtime unchanged until a replacement passes its gates. This assessment does not demonstrate a second file's performance.

Baseline: e219197b4494b125c0c15df301af0466b4bd1da9. Branch: feature/phase-9-writer-architecture. Only documentation, structured evidence and a read-only audit script were added. No persistence architecture, production database, contract or inference policy changed.

## Evidence and interpretation

| Series | Measurement definition | Preserved result |
| --- | --- | --- |
| Phase 8-B | Independent WAL writers, injected holds | 30.82 ms FAIL and 20.33 ms PASS |
| Phase 8-C | Same stress, shorter shadow preparation inside lock | Four of five added p95 failures, with no lost deterministic data |
| Frozen Phase 8-D | Admission with unchanged cadence and holds | Five passes; separate adversarial acquisition wait 50.21 ms |
| Frozen Phase 9-I legacy | Single-threaded analysis + save + issue read | Five isolated passes: 0.6269, 1.1937, 0.0013, 0.4721, 0.3807 ms |
| Original legacy failure | Same legacy case during concurrent validation | 30.9497 ms FAIL; cause unproven |
| Frozen Phase 9-I paired | Independent writers, persistence-only timing | Five passes, worst 24.9306 ms added p95 |
| Phase 9-I prod validation | Independent Phase 8-D pairs without unrelated project jobs | Four passes; pair 1 FAIL at 30.8624 ms added p95 |

The last failed pair had disabled persistence p95 2.4008 ms versus 33.2632 ms admitted. Acquisition p95 increased from 0.1300 to 26.2771 ms; acquisition maximum was 33.7333 ms and shadow hold maximum 33.7382 ms. Deterministic hold p95 also rose from 2.2935 to 6.6640 ms. All 100 samples and critical observations survived per mode. This supports acquisition wait as a major contributor in that fixture, not the sole cause of all variability. Acquisition includes OS scheduling while SQLite waits; hold includes execution, I/O and scheduling. No host I/O trace proves an exact decomposition.

The original 30.9497 ms legacy failure is separate and unexplained. Its loop and timing definition are not equivalent to the paired benchmark. Neither an isolated pass nor a zero process exit overrides an individual failed performance gate. The five-pair test reports a per-repetition passed flag but does not assert every latency gate; its zero exit is not latency acceptance.

Phase 9-J reuses saved observations. It does not rerun acceptance benchmarks or implement alternatives to collect favorable measurements. New existing-regression results establish unchanged behavior, not candidate performance.

## Actual ownership and collision paths

Current authoritative flow: Windows collector -> deterministic rules/history -> health.sqlite immediate sample transaction.

Advisory flow: committed sample ID -> bounded scheduler -> shadow Node thread -> telemetry export -> authenticated Python replay -> validated findings -> health.sqlite shadow transaction.

The same .ultron/health.sqlite contains authoritative samples, issues, settings, events and analyses plus every shadow table. Current writers/readers:

- scripts/health-worker.cjs commits samples, last-sample settings, lifecycle events and a separate ten-second heartbeat.
- lib/health/core.cjs sets WAL and a 5,000 ms deterministic connection timeout. saveSample begins an immediate transaction, updates issues/events/risk keys, prunes history and commits. API feedback, settings and analyses also write here. clear atomically removes both table families after stop checks, rotates the shadow installation ID, then checkpoints and vacuums.
- lib/health/shadow/thread.cjs opens a separate connection with a 50 ms busy timeout. Migrations, successful consume, and best-effort failure status/run recording write the same file.
- app/api/monitor/route.ts writes configuration, start/stop state, resample markers, feedback, analysis or history clear from a server connection. These writes are outside the collector activity flag.
- lib/health/shadow/observe.cjs uses a read-only connection, query_only, 20 ms busy timeout and one read transaction. It reads shadow tables and checks sample-anchor existence in the same file, without migration.

The stores cache is process-local, not an inter-process coordinator. The optional scheduler has one job and no pending queue, five-minute spacing and transient backoff capped at 30 minutes. The collector publishes activity across acquisition, analysis and persistence; it never waits for Python or a shadow acknowledgment.

### Why cooperative admission cannot guarantee the gate

writerAdmission.wait reads an atomic activity word and may immediately return idle. Nothing makes that check atomic with SQLite acquisition. Shadow can pass the check and acquire its writer before the next poll starts. Once held, the lock cannot be revoked by changing the flag. Its scope is one collector/worker pair, not API, heartbeat or maintenance writes. Failure recording checks idle once, not a reservation.

WAL still has one writer per database. The flag reduces collisions, not SQLite lock priority. A longer busy timeout changes waiting, not priority. Reserving a predicted poll interval is not a proof because Windows collection, resampling and commit durations vary. This is a check/acquire race in the cooperative boundary, not evidence of a broken atomic word.

### Reusable durability

Phase 8-C prepares statements, digests and serialized findings before BEGIN IMMEDIATE, and skips unchanged checkpoint updates. Inside one consume transaction it validates installation/policy/checkpoint, source anchor and final sample, repeat completeness, event digests, transitions, findings, hypothetical eligibility, rebase guard, pruning, checkpoint, status and successful run callback. Failure does not acknowledge consumed events. Reuse the existing rollback, interruption, overlapping replay and ownership tests.

Migrations use PRAGMA user_version 0/1/2. shadow_checkpoint.installation is a shadow-owned ID, not an independent authoritative dataset-generation fence. clearShadow currently participates in the health clear transaction. Merely changing a file path would break source checks, clear atomicity and dashboard anchor checks.

## Four-option comparison

The structured option_comparison.json covers all 12 requested criteria.

| Option | Benefit | Principal risk | Assessment |
| --- | --- | --- | --- |
| A Retain admission | Small; existing durability and accepted frozen runs | Recurring over-budget pair; nonpreemptible writer; uncoordinated writers | Retain as default-disabled baseline, not long-term assurance |
| B Single writer coordinator | Could prioritize pending deterministic writes | New collector/IPC dependency; active transaction remains nonpreemptible; all writers must join | Not preferred |
| C Separate shadow SQLite | Separates advisory writer lock, migration and corruption domain | Provenance/lifecycle complexity; shared hardware still contends | Preferred candidate, not a validated implementation |
| D Reduce/defer persistence | Reuse one-job/backoff; fewer attempts | Same collision when admitted; starvation, stale work and retention-induced rebase | Complement only |

No speedup is assigned to B/C/D. Repeated same-file interference and narrow pass margins justify testing a structural boundary. C avoids making authoritative collection depend on a new broker; unlike D it removes advisory write-lock sharing. Its lifecycle cost is justified only if separately approved tests pass.

**B:** A genuine single-writer coordinator must cover samples, settings, heartbeat, API, migrations and maintenance. Routing only shadow through a broker leaves other direct writers. Use typed authorized operations, bounded item/byte queues, commit acknowledgments and idempotent receipts. Prefer pending deterministic work and discard/coalesce advisory work before execution. A collector must not silently bypass a failed broker and create competing writers. No queue priority can interrupt a transaction already executing. This broad refactor adds an authoritative failure dependency.

**D:** Keep one in-flight attempt with no unbounded backlog. Discard or bound volatile output, retain the last durable cursor and export committed history later. Never pre-acknowledge deferred work. Sustained activity can starve shadow; missing retained history forces conservative bootstrap/rebase. Current spacing/backoff already supplies part of this approach.

## Proposed separate-store boundary

**Not implemented:** health.sqlite retains authoritative tables and its existing writer; a short read-only snapshot supplies sanitized replay to unchanged Python; one Node advisory writer commits the complete ledger to a fixed local shadow.sqlite.

Keep findings, transitions, consumed digests, checkpoint, eligibility, guards, runs and attempts together in the advisory file. Give it its own schema/version and explicit source-generation/provenance metadata. Python still gets neither SQLite access nor system-action authority.

### Atomicity and source changes

Materialize a bounded read-only source snapshot with dataset generation, sample IDs, times, content digest and analytical identity, and release the read before HTTP or advisory commit. Do not hold a health writer lock during inference, copy or shadow commit. Long-lived readers can prevent WAL reclamation, so snapshot duration and WAL growth require tests.

An authoritative dataset-generation lifecycle is a separately reviewed prerequisite, not a new health-side event queue. App restart must not rotate it. Clear/reset or incompatible restore fences old work; retention alone is not a reset. Preserve the shadow installation ID on a provable import; otherwise bootstrap as historical/uncertain, not eligible fresh events.

Keep the expected-checkpoint compare and complete ledger commit atomic in the shadow file. The present samples-exist SQL inside consume cannot move unchanged. Pre/post generation and boundary checks are necessary but do not create a cross-file atomic commit. A reset can race after a check: quarantine uncertain results, and check source generation when serving current/eligible state. Do not claim parity with same-transaction source validation until tested.

Keep eligibility and event receipt in the shadow transaction; do not duplicate receipts in health.sqlite and require a dual commit. Before-commit crash replays from durable state; after-commit retries deduplicate. Missing source, changed snapshot, lost anchors or mismatched restore must not create fresh eligibility. ATTACH is not a WAL group-atomic shortcut.

### Migration and operation

For a separately approved offline cutover, stop both workers, create/version advisory storage, import all ledger tables and IDs, verify counts/digests, save a migration receipt, then change a durable mode pointer. Resume interrupted import idempotently or stay in original mode. No dual writable paths or automatic fallback. Retain the original ledger as read-only reference until separately approved cleanup. Pointer and generation placement require review.

Collector startup must not await advisory storage. Missing, corrupt, out-of-space, incompatible or denied advisory storage is degraded/unavailable, not healthy. A read-only source opener must not silently create a new health database.

Existing sample retention is configurable 1/3/7 days with a 20,160-row cap; replay is bounded to 1,800 rows/12 hours; shadow pruning includes 500 attempts/runs and old namespaces. Separate retention must preserve active ownership and rebase guards. Never erase the only suppression evidence just because source rows expired.

The read-only dashboard must route to advisory tables with unchanged sanitized query bounds and a bounded source-generation/anchor check. Report missing, stale, incompatible and partially cleared stores explicitly. No fallback through createStore on a missing source.

Current clear is atomic across both table families. Future two-file clear cannot promise that. Reuse stop-before-clear, durably fence the old generation first, mark cleanup pending, delete advisory records/sidecars and confirm completion only after verification. Late commits from an old generation must be invisible/ineligible and cleaned later. Crash between steps resumes without resurrecting deleted data.

Backups must cover both files and matching provenance/cutover metadata, including WAL concerns. Use a tested offline/quiescent procedure or SQLite snapshot backup interfaces, not a naive live file copy. Separate snapshots are not a cross-file snapshot. Test partial restore and interrupted deletion. A second database increases privacy/deletion scope even without duplicating raw telemetry.

### Governance

The Phase 9 registry pins storage/ownership source hashes, including shadow/store.cjs. A future refactor changes these artifacts even if analytical thresholds stay identical. Introduce separately reviewed storage compatibility metadata and evidence; never rewrite frozen manifests or call changed storage a proven metadata-only equivalent. Keep scopes, persistence:1800:0.65, 240-second lead and phase8-offline-v1 unchanged.

## Proposed stages and acceptance

1. Review generation, receipts, migration/cutover, clear/backup/restore and read-only compatibility contracts before code.
2. Separately approve an isolated prototype with source read-only access and one advisory writer; keep collector and Python transport.
3. Validate migration, restart and deletion reconciliation plus dashboard state; no production activation.
4. Predeclare equivalent disabled, current same-file and candidate split-file comparisons. Keep workload intensity/holds unchanged, disclose file-routing changes and keep environmental stress separate.

Each of five declared repetitions per acceptance workload must meet **added deterministic persistence p95 <=25 ms**, with zero lost samples/critical findings and integrity checks on both stores. Report absolute p50/p95/max, acquisition wait, hold, overruns, read duration, WAL/checkpoint I/O and advisory freshness. A p95 gate is not a maximum-latency guarantee. Set extra resource/freshness budgets before evaluating.

Detailed gates in proposed_stages_and_gates.json require commit/import fault injection, source reset/retention/restore races, bounded scheduling, advisory disk-full/corruption/permission failures, auth/schema mismatches, replay/forecast transitions with zero duplicate openings, dashboard compatibility and zero actual notifications. Failure blocks integration; do not replace failed runs.

## Validation and frozen evidence

Existing Node checks run serially before unrelated validation: 12 replay cases excluding the historically measured latency gate, five operational/admission cases, coordinator, Phase 9 ownership, two Phase 8-C transaction cases and the five-repetition HTTP matrix. Existing Python, self-checks, build, types and Edge outcomes are separately recorded in validation_results.json. These are new regression outcomes, not new architecture acceptance measurements; first failures are preserved.

All those Node cases passed. All 269 Python tests passed with one existing dependency deprecation warning. Four self-checks, build, TypeScript, Edge and whitespace checks passed. New HTTP observations: five complete corrected-probe 413 responses and 20 successful recoveries; the legacy full-upload probe reset after 413 headers in repetition 4. This known transport observation was retained, not replaced. Ownership matched the frozen digest with zero duplicate logical/hypothetical openings and no actual ML notifications. No new architecture acceptance benchmark was run.

The read-only audit script hashes source with line anchors and verifies 583 historical entries, 39 Phase 8 outputs, 13 Phase 9 results, seven Phase 9-H outputs/config, eight Phase 9-I outputs/config and their archived original TAP files. Saved integration metrics retain acquisition and hold separately. It writes only into a new evidence directory. Original TAP remains local because it includes machine paths; published summaries are sanitized.

Primary SQLite documents checked on 19 November 2026: [WAL concurrency and per-file atomicity](https://sqlite.org/wal.html), [ATTACH multi-file commit limits](https://sqlite.org/lang_attach.html), and [snapshot backup](https://sqlite.org/backup.html). They establish capabilities, not ULTRON candidate performance.

## Release blockers and stop

Historical 12 duplicate notices still imply approximately 1.48 interruptions/device-day against 0.5. Controlled ownership tests do not establish live delivery safety. Real-device accuracy and drift calibration remain unverified. Split-store lifecycle and performance are proposed, not verified. Disk, CPU and read/checkpoint I/O contention remain after any lock split.

**Recommend C only for a separately approved design/prototype phase.** Current runtime stays unchanged. No Phase 10, persistent inference, live alerts, remediation, policy promotion/rollback, commit, push or merge was performed.
