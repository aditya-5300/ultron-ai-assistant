# ULTRON Phase 5 Offline Early Warning and Forecasting

**GO TO PHASE 6 WITH FORECASTING SCOPE: process.** The selected simple process-responsiveness policy met the predeclared synthetic acceptance gates. CPU/workload and disk capacity remain detection-only. This is a recommendation for separately approved offline follow-up, not permission to start Phase 6, connect production monitoring, issue live alerts, or claim crash or hardware-failure prediction.

## 1 Scope and frozen evidence

- CPU forecasting refers only to future sustained contextual workload pressure, not CPU failure.
- Disk forecasting refers only to a selected volume crossing a free-space boundary, not SMART, wear, physical drive failure, or hardware-health probability.
- Process forecasting refers only to future sustained nonresponse of monitored selected processes. Per-process identity is not available in this contract; ownership is scoped to the monitored subsystem. The score is not a crash probability.
- No forecasting targets were added for memory, battery, temperature, events or generic anomalies. RAM is supporting context only. The deterministic critical lane remains authoritative and independent.
- Verified all **410** prior reference hashes: Phase 4 (90), 4.5-A (102), 4.5-B (109), and 4.5-C (109). No prior data, labels, scores, configuration or release criteria were regenerated, rewritten or used for tuning. Phase 5 is local on `feature/phase-5-early-warning`, from approved `prod` commit `7e51b8fbec9ab91b6be47872ab274cd975354ae1`.

**Prior release caveat remains mandatory.** Phase 4.5-C recorded 12 duplicate process notices inside detected events; treating them as unwanted notifications raises the burden to about 1.48/device-day, above 0.5/day. Phase 5 tests a separate offline ownership policy. It does not recalculate that historical burden or establish deployment readiness.

## 2 Target contract

`phase5-targets-v1` was defined before predictor code and locked with inputs and implementation hashes. Prediction timestamp is acquisition **T**; only observations at or before T are eligible. A match needs **T < actionable onset ≤ T + horizon**. A warning at or after actionable onset is late/nonforecast, even if the causal detector only confirms later. Ground-truth onset uses a separate latent 15-second simulation clock. Observability requires an independent causal confirmation while the latent event is still active. A run with a missing crossing does not convert it to a healthy or observed event.

| Target | Actionable onset and recovery | Minimum useful lead | Candidate horizons |
|---|---|---:|---|
| CPU/workload | CPU ≥85% with RAM ≥85% or selected nonresponse count ≥2, for ≥300 s and ≥6 independent samples. Recover on CPU ≤72% or known context clearance for ≥120 s and ≥2 independent samples. | 180 s | 5, 10, 15, 30 min |
| Disk capacity | First fresh usable observation ≤10% free in the selected volume. Recover at ≥14% across fresh samples spanning ≥300 s. One fixed volume per run. | 900 s | 30 min, 1 h, 3 h, 6 h |
| Process responsiveness | Selected nonresponse count ≥3 for ≥360 s and ≥6 independent samples. Recover at count ≤1 over ≥120 s and ≥2 independent samples. | 240 s | 5, 10, 15, 30 min |

Continuity limits are 105 s for CPU/process and 780 s for disk. Cached disk samples retain the original measurement timestamp and cannot extend independent evidence. Missing/uncertain context resets pending confirmation rather than assuming good health. Forecasts with unresolved gaps, right-censored no-event windows or an unobservable matching event are classified uncertain and charged to the interruption budget. An event observed in a valid future window can be confirmed even if the run ends before the full horizon.

## 3 Data, baselines and leakage controls

| Partition | Independent seeds | Nominal exposure |
|---|---|---:|
| Development | 8101–8106 | 6 × 24 h |
| Validation | 8201–8206 | 6 × 24 h |
| Locked holdout | 8301–8310 | 10 × 24 h |

The fixtures vary normal load, interactive spikes, long and short builds, backups, contextual rising/oscillating CPU pressure, gradual and step changes in responsiveness, short benign stalls, stable near-threshold disk, linear and accelerating consumption, temporary consumption/cleanup, and recoveries. Irregular acquisition, deep cadences of 225/300/420 s, disk gaps, sleep, cache repeats and missing sensors exercise observability. These are repeated synthetic mechanism families, not evidence of generalization to real devices. A 24-hour horizon was excluded because 24-hour runs do not provide credible uncensored evaluation exposure at that horizon.

- **Persistence:** A guarded current-state/rolling-rate baseline. CPU requires context; process uses recent near-boundary nonresponse frequency and persistence; disk uses a near-threshold band without inventing an ETA.
- **Linear:** Fit past distinct observations and extrapolate directionally toward the target, adding target hold time where relevant. Poor fits, inadequate spans or moving away from the boundary do not produce a valid ETA.
- **Robust:** A bounded median-of-pairwise-slopes estimate with directional agreement and a development-only noise floor.
- **Disk ETA:** Interpretable linear threshold-crossing time with an additional fit-quality floor of 0.75, ≥5 distinct readings and ≥1,200 s span in a continuous 90-minute past window. Insufficient samples/span means insufficient evidence, not a fabricated estimate.
- **No forecast / detection only:** The same causal target detector remains active; it issues no forecast. It provides a detection and ownership reference rather than being credited for advance hits.

No learned model was trained or packaged. The simple baselines were sufficient to test whether advance evidence exists, so no model-comparison artifact or incremental learned-model claim applies. The robust floor used only development-run fresh differences: CPU 0.666 percentage points, disk 0.132 points, process 0.15 count. Its API rejects validation and holdout data. Phase 3 features and baseline state use only past/current telemetry; neither event onsets nor recoveries enter feature rows or state. Truth sidecars are read only after scoring a partition. Development and validation alone select from 80 predeclared subsystem/method/horizon/threshold points. Budget and useful lead are ranked before precision/lead; ties favor shorter horizons. The finalists and selected horizons were written before any holdout input opened. Holdout labels were opened after both independent scoring passes.

Input lock SHA-256: `54f9f5c35e13413fdf504a587b400332e85e1f2e455ebe840cdb37d48d175c62`. Frozen horizon selection SHA-256: `06624d125c50d8134308ef57195a0c596087faeac82913b50e166ac73363c542`.

## 4 Ownership, deduplication and cancellation

NORMAL has no active evidence; OBSERVE is silent. EARLY_WARNING opens only after independent fresh confirmations and elapsed evidence (three over 60 s for CPU/process; two over 180 s for disk). Each episode stores its stable ID, target version, first prediction, last confirmation, horizon and window end, score type, ETA when supported, evidence summary, state and closure/recovery reason. Scores are uncalibrated evidence, not probabilities.

One active owner per run/subsystem absorbs repeated evidence. An unexpired forecast linked to the causal detection becomes DETECTED without a second modeled advisory interruption. Detection without a forecast, or after an expired forecast, can still interrupt. Deterministic critical notices remain independent and are never suppressed by the forecast owner. Expiry is not recovery. Missing readings do not close the episode; cancellation and recurrence require fresh lower-band evidence plus an elapsed recovery interval, with a cooldown (180 s CPU/process, 600 s disk) before another forecast. Cancelled forecasts remain in the evaluation. A cancelled forecast before its target is not credited. Multiple forecasts for the same event, if present, are counted separately as duplicates and charged to nuisance.

## 5 Locked holdout comparison

Holdout exposure: **28,261 acquisition rows**, **9.898 monitored device-days**, **50** backup/build sessions and **20 observable actionable events per subsystem**. No latent target event was unobservable in these runs, though future coverage for some unmatched warnings was uncertain. Monitored time caps acquisition gaps at twice cadence. Lead times below are measured to latent actionable onset; lead distribution includes all confirmed strictly ahead forecasts, not only those above the minimum.

| Subsystem and method | Horizon | Any ahead / useful out of 20 | Episode precision | False or uncertain / day | Workload nuisance | Median lead |
|---|---:|---:|---:|---:|---:|---:|
| CPU detection only | — | 0 / 0 | — | 0.000 | 0/50 | — |
| CPU persistence | 5 min | 20 / 19 | 44.4% | 2.526 | 15/50 | 218 s |
| CPU linear (selected diagnostic) | 5 min | 0 / 0 | — | 0.000 | 0/50 | — |
| CPU robust | 5 min | 0 / 0 | — | 0.000 | 0/50 | — |
| Disk detection only | — | 0 / 0 | — | 0.000 | 0/50 | — |
| Disk persistence | 30 min | 20 / 4 | 66.7% | 1.010 | 10/50 | 546 s |
| Disk linear (selected diagnostic) | 6 h | 20 / 20 | 80.0% | 0.505 | 0/50 | 9,402 s |
| Disk robust | 30 min | 20 / 17 | 58.8% | 1.414 | 0/50 | 1,046 s |
| Disk ETA | 6 h | 20 / 20 | 80.0% | 0.505 | 0/50 | 9,402 s |
| Process detection only | — | 0 / 0 | — | 0.000 | 0/50 | — |
| **Process persistence (selected)** | **30 min** | **20 / 14** | **100%** | **0.000** | **0/50** | **835 s** |
| Process linear | 30 min | 14 / 14 | 100% | 0.000 | 0/50 | 1,410 s |
| Process robust | 5 min | 0 / 0 | — | 0.000 | 0/50 | — |

### Selected policies and interpretation

- **CPU — DETECTION ONLY.** The selected `linear:300:0.65` point issued no forecast, missed all 20 observable advance opportunities, and spent 0.56% of rows with insufficient evidence. Persistence shows advance signal but violated both budgets (25 unmatched interruptions; 30% workload nuisance). The causal detector can still detect current events; a quiet forecaster without recall does not pass.
- **Disk capacity — DETECTION ONLY.** `linear:21600:0.85` reached all 20 observable events with useful lead and 80% precision, but **5/9.898 = 0.5051477/day** misses the strict ≤0.5 budget. The point also missed the development budget (0.674/day), despite meeting it on validation (0.168/day). The five charged episodes cancelled; their negative windows were uncertain, not silently discarded. p25/median/p75/p95 lead: **6,614 / 9,402 / 10,242 / 10,880 s**. Insufficient evidence: **6.76%**. This is promising capacity-only evidence, not clearance to forecast live.
- **Process responsiveness — FORECASTING CANDIDATE.** `persistence:1800:0.65` qualified on development and validation (9/12 useful each, no false interruptions). On holdout, any-ahead recall **20/20**, useful recall **14/20 (70%)**, precision **20/20**, zero missed observable advance events, no late or duplicate forecast episodes, and 0/50 nuisance sessions. Six confirmed forecasts were earlier than onset but below 240 s useful lead. p25/median/p75/p95: **119 / 835 / 866 / 905 s**; insufficient evidence **0.45%**. Linear matched useful recall and gave longer lead on its narrower 14 hits; it did not cover all events. The selected policy was frozen before holdout and is not replaced based on this diagnostic comparison.

Future-positive row prevalence for the selected CPU/disk/process horizons was **0.765% / 66.4% / 4.67%** after excluding active, right-censored and known unobservable positives. The disk percentage reflects the broad six-hour target window and repeated fixture ramps, not a real failure rate. Event metrics—not row accuracy—drive the gate. Process useful recall has a descriptive Wilson interval **48.1–85.5%**; zero workload nuisances in 50 gives a descriptive upper bound **7.1%**, still above 5%. Synthetic evidence supports offline research, not a guarantee of live alert rates.

### Raw candidates and interactions

| Selected subsystem | Raw qualifying rows / starts | User-facing forecast episodes | Repeated raw confirmations absorbed | Detection notices avoided in modeled ownership |
|---|---:|---:|---:|---:|
| CPU | 0 / 0 | 0 | 0 | 0 |
| Disk capacity | 5,986 / 27 | 25 | 460 | 20 |
| Process | 487 / 20 | 20 | 420 | 20 |

Raw rows include cached positives where evidence remains usable; cached values cannot independently confirm. They are not notification counts. In the selected process replay there were no cancellations or duplicate forecast episodes. Twenty forecast owners transitioned to detection without a second modeled advisory, versus 20 standalone detections in the no-forecast reference. Disk produced 20 linked detections and five cancellations; nine critical disk openings remained independent. Across all signals the deterministic reference recorded **117 critical row checks and zero mismatches**. The offline process result does not prove the original Phase 4.5-C live-release caveat has been resolved.

## 6 Reproducibility, performance and gate

Two independent feature, predictor and lifecycle replays produced identical raw prediction digests, episodes, matching and acceptance. Decision SHA-256 for both: `10a3f88d7528f19c83c70145a5063bf7dfc9a243dc9bf49cac7cfcf3de0822e4`. Timings are excluded from decision digests. Incremental Phase 3 feature update plus scoring/lifecycle across the frozen policy set was **0.93 ms p95**, **0.77 ms median**, measured on 28,261 acquisition rows. This excludes collection, file I/O and report generation; it is not a production SLA.

| Gate | CPU | Disk capacity | Process |
|---|---|---|---|
| Global safety, causal tests, replay parity, p95 <100 ms | Pass | Pass | Pass |
| Pre-holdout usefulness and nuisance qualification | Fail | Fail | Pass |
| Holdout false/uncertain ≤0.5/day and workload nuisance ≤5% | Pass | Fail | Pass |
| ≥10 observable events, useful recall ≥40%, precision ≥50% | Fail | Pass | Pass |

Only process warrants the next separately approved offline step. More diverse traces, realistic transient stalls and independent workload regimes are needed. The target is a sustained count in selected processes, not per-process identity, causal diagnosis, application crash, OS failure or physical failure. The fixture mechanisms are stylized, and the no-forecast reference remains valuable for cases without enough advance evidence. Do not integrate into ULTRON runtime or enable live alerts.

## 7 Reproduction and validation

From the repository root with the existing ML environment, use new empty directories:

```powershell
python -m ultron_health_ml.forecast.replay prepare --inputs ml/generated/phase5/new-lock
python -m ultron_health_ml.forecast.replay evaluate --inputs ml/generated/phase5/new-lock --locked-sha256 <hash-from-prepare> --reference-root . --output ml/artifacts/phase5/new-report
```

The measured run is under `ml/artifacts/phase5/verified-v1`. It contains `forecast_target_contract.json`, `phase5_config.yaml`, `horizon_selection.json`, `baseline_comparison.csv`, `forecast_event_evaluation.csv`, `forecast_episode_evaluation.csv`, `lead_time_evaluation.csv`, `subsystem_forecast_evaluation.csv`, `nuisance_evaluation.csv`, `duplicate_notice_evaluation.csv`, `detection_forecast_interaction.csv`, `locked_holdout_manifest.json`, `reproducibility_manifest.json`, and `acceptance_gates.json`, plus the development/validation frontier. Omit `--reference-root` on a clone without local archives; do not claim those absent bytes were verified.

- **Files:** Added the `forecast` package, two config files, `ml/tests/test_phase5_forecast.py`, and this report. The ML README links the offline entry point. No dependencies added.
- **Tests:** 211 Python tests passed, including target thresholds, timing, as-of prefix invariance, future-label isolation, training-only preprocessing, cadence, insufficient ETA evidence, ownership, missing data, cancellation/recovery, no post-holdout selection, safety under forecast failure, and replay parity. ULTRON health fixture and command/agent/browser-tab/native-speech self-checks, the Next.js production build, TypeScript and Git whitespace checks passed. Native speech used generated audio.
- **Scope:** Windows worker, production SQLite, monitoring API/dashboard, controllers and LLM/RAG integration are unchanged. No FastAPI, MLflow, deployment, live inference, automatic remediation, commit, push or merge. Phase 6 has not started.
