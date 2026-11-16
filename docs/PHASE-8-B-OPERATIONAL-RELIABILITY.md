# ULTRON Phase 8 B Operational Reliability

**Decision: REPEAT PHASE 8.** The actual Windows collector restarted successfully in isolation, and the authenticated service enforced its 2 MiB request limit. Neither run lost deterministic samples or critical findings. However, one independently timed SQLite run exceeded the predeclared 25 ms p95 regression limit. The second run passed; it does not erase the first failure. Do not start Phase 9 or enable live inference notifications.

## Frozen baseline and test environment

The SHA-256 manifest in `docs/phase8b-reference/phase8a_sha256.json` freezes 29 Phase 8-A changed files: implementation, policy, deterministic fixture generators, test configuration, measured outputs, acceptance results, reproducibility manifest and report. All 514 older frozen reference hashes were checked. Phase 8-B uses new test files and a separate results directory. No existing source, policy, service contract, dashboard, or Phase 8-A report was edited.

Tests ran on Windows 11 with Node 24.18.0, the existing Python environment, and local Edge for dashboard verification. The fixtures create temporary SQLite databases, isolated collector copies, ephemeral ports and test-only service tokens. Native Windows telemetry stays in temporary databases and is not included in reports. There is no production database connection or persistent shadow activation. PowerShell collection, the deterministic analyzer, WAL persistence and the shadow worker were not mocked in the collector test.

## Collector lifecycle and service outage

The actual `health-worker.cjs` ran with its unmodified PowerShell collector. A deterministic synthetic history prefilled replay warmup; subsequent samples were actual read-only Windows acquisitions. The worker persisted samples and existing analysis during startup connection refusal, shut down through its normal configuration flag, restarted cleanly, and recovered from two abrupt terminations after committed shadow work. The first abrupt restart also restarted Python. Recovered runs reused the durable namespace and retained one historical process owner, with zero newly eligible openings. SQLite quick check passed.

In the final run, startup-to-collection took **3.85–5.44 s**. Startup-to-successful shadow inference on recovered starts took **4.48–6.18 s**. These are isolated test observations, not production guarantees. This test did not force termination at every requested point inside the telemetry-to-shadow persistence sequence, after the maximum backoff, or after an actual collector-driven rebase.

A separate test drove the production scheduler with an injected virtual clock and a real Node worker and loopback service. Five refused connections caused delays of **5, 5, 10, 20 and 30 simulated minutes**; 10 triggers were skipped while busy or backing off. The same scheduler accepted its first permitted probe after service restoration, with no restart and no eligible opening. The recorded recovery time starts at probe dispatch and excludes Python startup. Authentication rejection, metadata mismatch, timeout and partial responses were covered in the combined Phase 7/8 suite, not as a multi-day fault sequence inside the Windows collector.

## Concurrent writes and deterministic isolation

The two writers used independently scheduled threads and separate SQLite connections. The deterministic side executed the existing analyzer and `saveSample` on 100 identical critical disk samples per mode. The candidate side ran shadow `consume` and run recording at an accelerated cadence against the same WAL database. A connection-local test trigger held the shadow transaction for 5 or 20 ms on selected writes. No production schema or polling cadence changed. This is a deliberately stressed persistence benchmark, not a production latency estimate.

| Run | Disabled persistence p95 | Shadow persistence p95 | Added p95 | Gate |
| --- | ---: | ---: | ---: | --- |
| Initial | 1.92 ms | 32.74 ms | 30.82 ms | FAIL |
| Final | 2.00 ms | 22.34 ms | 20.33 ms | PASS |

Both runs retained all 100 samples and all 100 critical findings in each mode. Each candidate completed 100 shadow writes with zero SQLITE_BUSY results; waiting on the shared writer lock still increased tail latency. Maximum deterministic persistence was **63.44 ms** in the initial candidate, and **43.72 ms** in the final candidate. The CSV includes analysis, persistence, poll overruns and shadow transaction median, p95 and maximum. Phase 8-A separately demonstrated busy rejection and rollback. Restart or outage during the concurrent writer benchmark and existing monitor API latency under contention remain unverified. A database write lock can prevent immediate failure persistence in observability.

## HTTP request limits and replay ownership

The actual Node client and Phase 6 Python service accepted an unpadded **1,435,945-byte** request containing 1,800 observations. A separate near-12-hour window also passed. The same valid 1,800-row document padded with legal JSON whitespace was accepted at **2,088,960 bytes** and at exactly **2,097,152 bytes**. At **2,097,153 bytes**, the service returned **413 REQUEST_TOO_LARGE** and the client rejected the request. No observation was truncated. This proves the HTTP byte boundary; it does not prove natural exporter growth reaches the cap or characterize long-run memory.

The combined accelerated replay covered three simulated days of input, not three days of Windows operation. Across 30 requests it validated 1,116 raw transitions, ignored 756 repeats, persisted 360 transitions and performed five rebases. Six openings were hypothetically eligible; no replay duplicate opening was reported and no notification was sent. The combined tests also cover forecast-to-detection ownership and closure, retention, sleep gaps, stale responses and an ambiguous rebase followed by genuine recovery. They do not combine every fault with the full Windows process.

Two additional independent databases used the same fixed test installation ID and deterministic history. A Python restart separated the runs. Exact finding identities, transitions, consume results and eligibility decisions matched. The Phase 8-B decision digest is `d086e5d9e640e1ed64d86bf5043c4a3ac2f16f46e7584801161bad062a5a297d`; operational latencies are separate, not required to match.

## Validation and release gate

Five Phase 8-B Node tests, 13 combined Phase 7/8 Node tests, 234 Python tests, the Edge dashboard test, ULTRON command/agent/browser/native-speech/health self-checks, the Next.js production build, TypeScript and Git whitespace checks passed. Python reported one existing dependency deprecation warning. Native speech used generated audio. The **operational latency gate failed in one run despite passing test assertions**; the benchmark records that outcome rather than discarding the measured evidence.

The read-only dashboard and API, default-off switch, local authentication, sanitized export, and `phase8-offline-v1` eligibility policy are unchanged. No secrets, raw telemetry, or exception traces are included. No live ML alerts or automatic remediation were used. The 12 historical duplicate process notices remain a deployment blocker: counting them would raise the interruption rate to approximately **1.48/device-day**, above the **0.5/device-day** limit.

Added `scripts/phase8b-fixture.cjs`, `scripts/phase8b-operational.test.cjs`, `scripts/phase8b-writer.cjs`, `scripts/phase8b-report.py`, this report, and the configuration, manifests, CSV and JSON evidence under `docs/phase8b-reference/`. The tests run with `ULTRON_PHASE8B_PYTHON` set to the project virtual-environment interpreter and `node --test --test-reporter=tap scripts/phase8b-operational.test.cjs`. Run the existing authenticated replay harness with `python scripts/phase7-e2e.py --phase8`; feed both successful TAP files into `scripts/phase8b-report.py` with a new output directory.

Before reconsidering Phase 9, reduce deterministic writer delays without changing detection rules, rerun the same predeclared workload, and add full-collector pending-inference interruption, extended outage/rebase, and contention-recovery coverage. No commit, push, merge or Phase 9 branch was created.
