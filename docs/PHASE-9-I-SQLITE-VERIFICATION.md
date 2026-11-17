# ULTRON Phase 9-I Isolated SQLite Verification

**Decision: SQLITE ISOLATED GATE VERIFIED.** Five unchanged isolated runs met the 25 ms added p95 gate. The original 30.95 ms result is still a failure. This is a fixture result, not approval to activate production inference.

## Original failure and measurement

At baseline `6dac1f1a8d976482dec85b315c95d97c2f1e922c`, the Phase 9-H integration case `equivalent deterministic workloads survive overlapping shadow writes and bounded lock rejection` in `scripts/phase8-shadow.test.cjs` measured 30.9497 ms added p95 against a 25 ms limit. Both modes kept all 40 deterministic samples and all 40 critical observations. The byte-identical TAP remains locally archived outside Git; its SHA-256 is `5ef6934b628315f38bbc8cf6a22381dafa6efbc2e92596c76c85ed1dc7a14007`. Python and frontend validation jobs were launched alongside the original replay run; the exact active set at each measured sample and any causal effect are unknown.

The unchanged legacy test seeds 181 rows per mode, then measures 40 deterministic samples each with shadow disabled and shadow enabled. The store uses WAL and a 5,000 ms deterministic busy timeout; the test's second connection uses 10 ms. Every fifth shadow attempt (eight per run) is made while the main connection explicitly holds a write transaction; the other 32 attempts consume an empty batch. This is a single-threaded synthetic loop with no timed lock hold, admission signal, or real collector cadence. It times analysis, save, and critical-issue read together, then subtracts the disabled nearest-rank p95 from the shadow p95. It has no per-transaction acquisition or hold measurements. The production collector instead uses a default 30-second cadence with a configurable interval and a separate shadow scheduler; it signals activity across collection and persistence when explicitly enabled.

## Five isolated runs and background comparison

Windows 11 build 26300, Ryzen 5 5600H (6 cores, 12 logical processors), 7.36 GiB OS-visible RAM, NVMe fixture storage, Node 24.18.0 and SQLite 3.53.1. Five independent test processes ran in the declared order, without concurrent project validation. Ordinary OS and desktop activity was not disabled.

| Run | Added p95 (ms) | Gate |
| --- | ---: | --- |
| Original concurrent run | 30.9497 | FAIL |
| Isolated 1 | 0.6269 | PASS |
| Isolated 2 | 1.1937 | PASS |
| Isolated 3 | 0.0013 | PASS |
| Isolated 4 | 0.4721 | PASS |
| Isolated 5 | 0.3807 | PASS |
| Stress 1 | 0.1808 | Comparison only |
| Stress 2 | -33.7700 | Comparison only |
| Stress 3 | 1.0871 | Comparison only |

The predeclared stress experiment used two CPU workers (40 ms busy, 25 ms pause) and a worker overwriting and flushing a single 256 KiB temporary file, pausing 20 ms between writes. It used the same legacy workload and did not reproduce the original positive delta. Stress run 2 instead raised the **disabled** p95 to 36.13 ms and its maximum to 186.22 ms; the shadow p95 was 2.36 ms. The modes execute sequentially, so a negative difference does not imply a speedup. The observed variation shows why absolute latencies and controls matter, but does not identify what caused the original failure.

## Separate Phase 8-D comparison

Ran the unchanged paired benchmark once, containing five repetitions with the disabled, Phase 8-C and Phase 8-D modes. Each mode uses 100 deterministic writes at a nominal 12 ms cadence, with 100 shadow attempts at 9 ms when enabled, including simulated 5 and 20 ms holds. Shadow uses a 50 ms busy timeout; the activity wait polls every 4 ms for up to 250 ms. This benchmark times persistence alone and separately traces acquisition, lock hold, and overruns. It is not a rerun of the original gate.

| Repetition | Disabled p95 (ms) | Phase 8-C p95 (ms) | Phase 8-D p95 (ms) | Phase 8-D added p95 (ms) |
| --- | ---: | ---: | ---: | ---: |
| 1 | 2.18 | 22.23 | 27.11 | 24.93 |
| 2 | 22.28 | 24.69 | 34.10 | 11.82 |
| 3 | 2.10 | 34.06 | 18.70 | 16.60 |
| 4 | 2.01 | 34.03 | 20.79 | 18.79 |
| 5 | 2.13 | 47.94 | 21.29 | 19.16 |

Each Phase 8-D added p95 was within 25 ms, with the first pair close to the limit. The disabled maximum transaction acquisition was 0.22 ms, versus 76.66 ms in Phase 8-C and 47.42 ms in Phase 8-D. Phase 8-D shadow locks held for up to 35.97 ms; deterministic hold reached 57.08 ms. The Phase 8-D maximum deterministic overrun was 70 ms and its maximum shadow schedule delay was 608 ms. Even the disabled comparator overran by up to 129 ms. Acquisition time includes OS scheduling while the call is pending; the reported lock holds and schedule delays narrow the diagnosis without separating every OS delay from SQLite contention.

Phase 8-D waited for admission on 128 of 500 shadow attempts in this comparison; no attempt expired or returned SQLite busy. The separate deliberately stalled admission fixture exercised bounded refusal: it preserved the checkpoint with no successful write recorded, then retried and reconciled without duplicate logical or hypothetical openings. The coordinator observed refusal at 259.42 ms elapsed under a configured 250 ms deadline; timer scheduling can delay observation. The already-admitted writer fixture measured 32.63 ms of deterministic acquisition wait and preserved the sample and finding. Cooperative admission cannot preempt an active writer and does not guarantee latency or strict priority.

## Integrity and regression results

No deterministic sample or critical finding was dropped in the original, isolated, stress, or paired runs. SQLite integrity checks passed in the paired and admission fixtures; the unchanged legacy test asserts preservation and bounded busy errors but does not run its own `quick_check`. The existing scheduler regression confirmed one outstanding job, backoff, and recovery without unbounded buffering. Admission retry preserved checkpoint atomicity. The Phase 9 ownership digest matched the frozen value with zero duplicate logical openings, zero hypothetical duplicates, and no actual notifications.

Passed: 12 remaining Phase 7/8 replay cases (the isolated gate was already run five times), five Phase 8 operational and admission cases, the bounded coordinator test, Phase 9 ownership, all 269 Python tests with one existing deprecation warning, Edge dashboard, four ULTRON self-checks, Next.js production build, TypeScript, and Git whitespace. The Phase 9-H HTTP matrix ran five repetitions: all corrected probes returned complete 413 responses and all 20 recovery checks passed; two legacy raw resets were recorded separately without a retry to replace them.

All 583 historical hashes, 39 Phase 8 benchmark hashes, 13 Phase 9 saved-result hashes, and seven Phase 9-H output hashes plus its configuration checked out. The original failure, frozen Phase 8 comparators, Phase 9-H evidence, and thresholds were not overwritten. See `phase9i-reference/` for predeclared settings, per-mode latency summaries, admission and HTTP results, and new checksums. The checksums also cover raw TAP retained locally. The runner and exporter are `scripts/phase9i-run.cjs` and `scripts/phase9i-evidence.py`; use separate fresh output directories and keep other validation jobs idle during the measurements.

## Recommendation and limits

Do not change the production writer based on this investigation: no reproducible implementation defect in the original test was established. Keep isolated performance gates and separate stress results. Investigate further with transaction and host I/O traces if isolated gate failures recur. Shared SQLite writer contention, historical duplicate notices (1.48 versus 0.5 interruptions per device-day), and real-device validation remain open. No runtime, policy, branch target, or deployment behavior changed. No commit, push, merge, Phase 10 work, persistent inference, alerts, or remediation was performed.
