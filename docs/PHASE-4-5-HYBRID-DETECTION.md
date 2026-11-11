# ULTRON Phase 4.5 Hybrid Detection Review

**Decision: REPEAT PHASE 4.5.** The rules-first hybrid caught all six **observed** critical cases without dropping or delaying a deterministic alert, and its offline processing met the latency target. It did not meet the nuisance limits or demonstrate superiority at comparable alert budgets. Do not integrate it live or begin Phase 5.

Branch: `feature/phase-4-5-hybrid-detection`, based on approved `prod` commit `8b9312bf1f98c8139aa9054fdea5d641398bb088`. This is an offline synthetic experiment; neither novelty nor capacity exhaustion is evidence of physical hardware failure.

## Frozen Phase 4 reference

The Phase 4 system-wide Isolation Forest was rejected because it missed both disk-exhaustion cases, trailed both baselines on F1, and produced 44 false episodes per simulated day. All 90 Phase 4 reference files (both saved artifact sets and associated generated inputs) are checked against the previously verified digests in `ml/configs/phase4_reference_sha256.json`. They were not regenerated, rescored, reinterpreted, or overwritten. Phase 4 test runs 301–302 were not used for Phase 4.5 selection or acceptance.

| Unchanged Phase 4 metric | Fixed thresholds | Past-only MAD | Isolation Forest |
|---|---:|---:|---:|
| Row precision | 0.573 | 0.567 | 0.515 |
| Row recall | 0.416 | 0.541 | 0.319 |
| Row F1 | 0.482 | 0.554 | 0.394 |
| False episodes per simulated day | 6.0 | 12.0 | 44.0 |
| Disk-exhaustion cases detected | 2/2 | 1/2 | 0/2 |

Phase 4.5 uses a different holdout, actionable windows, and per-subsystem episode attribution. The table above is historical context, not a head-to-head comparison. The original fixed and MAD row rules and their operational variants were replayed on the **same new holdout** as the hybrid. The old forest was not rerun or carried forward as a candidate.

## Offline detector design

- **Safety pass first.** On usable readings, selected-volume free space **<5%** and RAM usage **≥95%** open or upgrade a critical episode on the same replay sample, before evaluating any statistical subsystem. The disk boundary mirrors the existing critical space rule; the RAM threshold is an explicit *offline* policy, not a change to ULTRON's production memory rule. A still-valid cached crossing is usable for safety, but never increments fresh confirmations. Tests inject failure in the statistical and optional paths, including alongside missing sensors. Production rules retain responsibility for any existing safety conditions outside the Phase 3 profile.
- **Subsystems.** CPU/workload uses high load plus RAM pressure or nonresponse for context. Memory uses level plus directional residuals and growth; the hybrid also considers an explicit high-RAM threshold for advisory evidence. Disk capacity separates low space from signed decline using distinct readings and longer windows; one fixed simulated volume per run is assumed. There is no volume identity or free-byte feature in this offline contract, and no cross-volume inference. Disk health is limited to temperature, not SMART, wear, or physical failure. Unplugged drain is separate from capacity degradation; capacity is tracked for coverage only. Process responsiveness uses selected-process counts, and event activity uses bounded event counts, not diagnostic attribution.
- **MAD.** Reads only Phase 3 past-only robust Z, requires valid baseline coverage and at least five distinct baseline observations by default, and applies subsystem direction, absolute noise floor, and persistence. No retrospective runtime CPU baseline is used.
- **Sustained change.** Requires a valid elapsed-time window and enough distinct observations, signed slope and minimum net movement, and subsystem-level readiness. Uses 15-minute windows for CPU/RAM/drain and 60 minutes for slow deep sensors; early exits and misses are reported, not predicted threshold crossings.
- **EWMA and CUSUM.** The elapsed-time EWMA uses a 150 s time constant versus a slower 1,800 s reference. One-sided CUSUM accumulates deviations normalized by a noise floor, with drift and a cap; its experiment is enabled only for memory and process. Both warm up on distinct fresh readings and reset after unsupported gaps. These are experimental drift indicators, not statistical control-limit guarantees. Slowly refreshed counts, power transitions, and unavailable channels cannot support arbitrary CUSUM use.
- **Fusion and lifecycle.** One episode per subsystem records its ID, severity, state, opening/last confirmation/closure times, sources, and supporting scores. Noncritical entry needs both a configurable number of independent observations and elapsed evidence (e.g. memory 3 over 180 s; disk 3 over 560 s). CPU requires 6 over 240 s. Duplicate cached measurements do not confirm, extend evidence, or recover an episode. On recovery, score must be below the lower boundary (55% of entry for statistical methods), or its readiness condition must clear, and valid fresh telemetry must persist for the subsystem's recovery duration. Critical episodes also require a safe raw clearance margin. Missing/stale data resets pending confirmations; after 180 s without confirmation an open episode can be marked insufficient, never healthy or closed by a missing reading. A new notice within ten minutes of same-subsystem closure counts as flapping. Gaps do not carry confirmation streaks forward.

## Events, data expansion and locked holdout

An event has an ID, kind, subsystem, start, end, actionable onset, simulated incident time where applicable, latent recovery time, observation-grace deadline, severity, and observable metrics. Matching requires a same-subsystem notification in `[actionable_onset, event_end)`, with at most one event credited per episode. Early openings before actionable onset are not credited unless there is an in-window critical upgrade; this is intentionally strict and can count plausible early signal as a false advisory. Cross-subsystem alerts do not automatically get credit for a related event. Recovery delay is measured from the fixture's latent reset at the end of injection; the separate recovery deadline adds a bounded observation grace period. Delay distributions are conditional on detections and must be read alongside recall. The critical gate compares the alert to the first usable observed crossing at or after the latent incident; no crossing seen within the event is counted separately as unobservable. Row scores are secondary: any actionable event active versus any flagged subsystem.

The fixture generator reuses Phase 2 causal clocks, sensor cadence and caches, and reuses Phase 3 feature engineering without changing it. Seeded overlays vary normal baseline load, memory, free space, temperature and battery capacity, as well as irregular sampling, missing sensors, sleep gaps, power plan/AC contexts, builds (long/short), backups, bursts, temporary RAM/disk activity, stable high RAM, and sustained legitimate CPU. Fault trajectories vary by family, order, onset, duration, intensity and sensor alignment. Training runs are benign; these methods fit no offline ML estimator. Normal development runs plus independent validation runs were used to select one of four predeclared threshold multipliers. None of the hybrid development candidates satisfied the nuisance budget; the least-over-budget candidate (2.0, 3.46 false/day and 20.8% workload nuisance on development) was frozen before the holdout opened.

| Partition | Independent seeds | Nominal exposure |
|---|---|---:|
| Benign development | 4501–4504 | 4 × 12 h |
| Validation | 4511–4516 | 6 × 12 h |
| Locked holdout | 4521–4526 | 6 × 12 h |

The input lock SHA-256 is `809b47da0167abf9be21bb4159d782335a701ebe9031a1eff64c3bed27014e9a`. The holdout manifest binds each run's telemetry, label and generator-manifest hashes. Selection is written to disk before opening any holdout telemetry; labels are opened only after replay. The new holdout has **8,381 rows**, **24 actionable events**, **15 backup/build sessions**, and **2.946 monitored device-days** (adjacent gaps capped at twice the nominal cadence so sleep is not credited as monitored time).

## Phase 4.5 locked holdout results

The standalone statistical experiments retain the same independent critical pass. “Original” methods preserve the original row rules, assigning them to a subsystem for episode matching; they did not emit critical severity, so the critical safety guarantee applies to the shared-safety methods, not those historical row rules. “Budget” comparators use the shared critical pass, the same lifecycle framework, and a development-selected threshold. Configuration and all candidate evaluations are saved.

| Method | Selected multiplier | Actionable events | Delay median / p95 | False noncritical / day | Workload nuisance | Row precision / recall / F1 |
|---|---:|---:|---:|---:|---:|---:|
| Original fixed | 1.0 | 11/24 | 1,394 / 1,720 s | 20 / 6.79 | 7/15 | .365 / .255 / .300 |
| Original past-only MAD | 1.0 | 1/24 | 1,248 / 1,248 s | 72 / 24.44 | 12/15 | .250 / .387 / .304 |
| Fixed with budget policy | 2.0 | 8/24 | 1,387 / 1,828 s | 5 / 1.70 | 2/15 | .444 / .143 / .217 |
| MAD with budget policy | 2.0 | 8/24 | 1,154 / 1,620 s | 23 / 7.81 | 10/15 | .325 / .344 / .334 |
| Subsystem MAD | 1.5 | 8/24 | 315 / 1,828 s | 6 / 2.04 | 1/15 | .504 / .250 / .334 |
| Sustained change | 2.0 | 14/24 | 281 / 1,320 s | 7 / 2.38 | 1/15 | .711 / .440 / .544 |
| EWMA | 2.0 | 15/24 | 234 / 1,756 s | 10 / 3.39 | 2/15 | .639 / .441 / .522 |
| CUSUM | 2.0 | 9/24 | 174 / 1,818 s | 10 / 3.39 | 2/15 | .631 / .420 / .505 |
| **Hybrid without ML** | **2.0** | **16/24** | **423 / 1,314 s** | **14 / 4.75** | **2/15** | **.668 / .618 / .642** |

The hybrid's 66.7% recall has a descriptive 95% Wilson interval of **46.7–82.0%**, ignoring run clustering. Nuisance is **13.3%** (2/6 long builds, 0/6 backups, 0/3 short builds; descriptive Wilson interval **3.7–37.9%**). It showed **zero flaps** and **zero duplicate advisories** under the stated definitions, median episode duration **1,732 s**, median recovery delay **232 s** among detected events whose episodes closed, and **200/61,412 eligible subsystem rows (0.326%)** without usable telemetry. That coverage metric does not count plugged-in drain as a missing reading, or include statistical warmup. Rows before an actual actionable onset and alerts for another subsystem are intentionally not credited. On diagnostic inspection, four process advisories fell inside CPU-contention episodes and two drain advisories opened before their actionable onset; five memory advisories occurred during benign workloads, including the two long builds. Do not change labels or thresholds to improve these held-out numbers.

| Hybrid subsystem | Detected/actionable | Median delay | False advisories | Missing/eligible rows |
|---|---:|---:|---:|---:|
| CPU/workload | 2/4 | 21 s | 2 | 19/8,381 |
| Memory | 6/6 | 162 s | 5 | 59/8,381 |
| Disk capacity | 4/6 | 1,185 s | 0 | 57/8,381 |
| Disk health (temperature only) | 1/2 | 1,361 s | 0 | 18/8,381 |
| Unplugged battery drain | 0/2 | — | 2 | 4/2,745 |
| Battery capacity (coverage only) | 0/0 | — | 0 | 15/8,381 |
| Process responsiveness | 1/2 | 9 s | 5 | 9/8,381 |
| Event activity | 2/2 | 633 s | 0 | 19/8,381 |

**Critical results:** 49 per-sample checks, zero drops; **6/6 observable critical cases** (four RAM, two disk) notified without delay relative to the first usable crossing. Two additional disk cases had no usable critical-threshold observation within the event; the hybrid produced earlier noncritical notices but could not verify a critical crossing. Disk gradual-decline detection was 0/2. This is a coverage and detection limitation, not a statement about future drive failure.

## Acceptance decision

| Gate | Result | Evidence |
|---|---|---|
| No critical drop or delay | **Pass for observed cases** | Zero misses/delays; failure and partial-telemetry tests pass. Two latent critical cases lacked a usable crossing. |
| Incremental value at comparable budget | **Fail** | Raw recall gain is +33.3 points versus both budget-policy baselines, but 4.75/day versus 1.70 and 7.81/day is outside the predeclared ±0.5/day tolerance for both. Delay differences are not evidence of superiority at a matched budget. |
| False noncritical episodes ≤0.5/day | **Fail** | 14 episodes / 2.946 monitored device-days = 4.75/day. |
| Backup/build nuisance ≤5% | **Fail** | 2/15 sessions (13.3%). |
| Incremental p95 <100 ms | **Pass** | 1.92 ms for Phase 3 feature update plus one hybrid step, across 8,381 samples on local Windows (median 1.21 ms). Collection, file IO, labels, and report generation are excluded. This is not a production SLA. |

**Optional ML was not run.** A hybrid-plus-ML comparison is not reported: the simpler hybrid failed its operating-point gates, so adding a learned model is not justified by this experiment. Sustained change is a useful lower-noise starting comparator for a separately approved follow-up. Use fresh development and acceptance runs to test stronger benign-memory and process corroboration, and predeclare how to score early notices that persist into actionability. Report no new performance claim on the inspected holdout. Simulation diversity is limited, synthetic time does not validate live alert rates, and sensor quality/identity in real deployments remains untested.

## Reproduction and outputs

Run from the repository root with the existing ML environment. The prepare step prints the input lock hash; save it before evaluation. Use empty, separate directories. The reference-root flag is read-only; on a clean clone without the local Phase 4 exports, omit it and consult the recorded checksums. No output may be written under the frozen Phase 4 directories.

```powershell
python -m ultron_health_ml.hybrid.replay prepare --config ml/configs/hybrid_detection.yaml --inputs ml/generated/phase45/new-lock
python -m ultron_health_ml.hybrid.replay evaluate --config ml/configs/hybrid_detection.yaml --inputs ml/generated/phase45/new-lock --locked-sha256 <hash-from-prepare> --phase4-reference-root . --output ml/artifacts/phase45/new-report
```

The measured run is under `ml/artifacts/phase45/hybrid-verified-v1`. Outputs: `event_definitions.json`, `hybrid_config.yaml`, `event_evaluation.csv`, `episode_evaluation.csv`, `subsystem_evaluation.csv`, `alert_budget_comparison.csv`, `detector_comparison.csv`, `locked_holdout_manifest.json`, and `reproducibility_manifest.json`. Extra audit files record candidate summaries, frozen selection, per-run results, workload sessions, and gates. The reproducibility manifest records the runtime versions, detector source hashes, feature schema/fingerprint, inputs, config and decision digest. Both replays produced decision SHA-256 `793a77ff23e19adccfeebe60bcc0d8da113c2f989ad7e042abc2b32c4e75b95f`; frozen selection, budget comparisons, workload sessions and holdout manifests matched byte for byte. Timing values are intentionally excluded from the decision digest.

## Files and validation

- **Created:** `ml/src/ultron_health_ml/hybrid/__init__.py`, `config.py`, `datasets.py`, `signals.py`, `detector.py`, `evaluation.py`, and `replay.py` in the hybrid package; `ml/configs/hybrid_detection.yaml`; `ml/configs/phase4_reference_sha256.json`; `ml/tests/test_hybrid_detection.py`, `test_hybrid_evaluation.py`, and `test_hybrid_replay.py`; this report.
- **Modified:** `ml/README.md` to document the offline entry point. No existing detector or feature-engineering implementation changed. No dependency added or file removed.
- **Validation:** 179 Python tests passed, including safety priority, fault isolation, persistence, hysteresis, cache handling, recovery, MAD/sustained/EWMA/CUSUM, fusion, event matching, alert-budget gating, holdout integrity/order and deterministic replay. Existing health fixture, command, agent, browser-tab and native-speech checks passed. The Next.js production build, TypeScript `--noEmit`, and Git whitespace checks passed. Native speech used generated audio, not a live microphone.
- **Scope and Git:** Windows worker, SQLite, API, dashboard, controllers and LLM integration were untouched. No live integration, FastAPI, MLflow, RAG, or Phase 5. The phase is ready for review, not promotion; no commit, push, pull request, or merge was performed.
