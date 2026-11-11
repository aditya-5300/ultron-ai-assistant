# ULTRON Phase 4.5-C Reduced Scope Validation

**GO TO PHASE 5 WITH REDUCED SUBSYSTEM SCOPE.** On the new synthetic holdout, all four frozen operating points met the same nuisance limits. The hybrid caught more actionable events than fixed thresholds or past-only MAD and caught the same events as sustained change with materially shorter median delay. This supports requesting approval for offline Phase 5 research in CPU/workload, disk capacity and process responsiveness only. It does not approve deployment, forecasting work in this phase, or a claim that ULTRON predicts hardware failure.

## Frozen references and reduced scope

- **Reference integrity:** SHA-256 verification passed for **90 Phase 4**, **102 Phase 4.5-A**, and **109 Phase 4.5-B** reference files. The prior holdouts, labels, results, configs and reference source covered by those manifests were not regenerated, rescored, edited, or used for C selection.
- **Historical context only:** Phase 4's Isolation Forest missed 2/2 disk-exhaustion cases and emitted 44 false episodes per simulated day. Phase 4.5-A's full hybrid detected 16/24 actionable events at 4.75 false episodes/day. Phase 4.5-B's selected policy detected 28/36 at 0.45/day, but no required baseline met that evaluation's budget. These are different holdouts, not head-to-head comparisons.
- **Primary advisories:** CPU/workload, disk capacity and selected-process responsiveness only. RAM provides context for CPU but does not open a proactive warning. Battery drain, capacity, disk temperature and event activity cannot open proactive warnings in C; the prior offline implementations are unchanged.
- **Global safety:** Every method runs the unchanged A safety pass first: selected-volume free space <5% and RAM usage ≥95% on usable telemetry. The latter is an offline policy, not a production change. RAM critical alerts are assessed for safety and nuisance, but RAM events do not inflate primary recall. A usable cached disk crossing can trigger safety; it cannot count as fresh advisory confirmation or recovery.

## New inputs and predeclared protocol

| Partition | New seeds | Nominal exposure | Primary events |
|---|---|---:|---:|
| Development | 7101–7105 | 5 × 18 h | 30 |
| Validation | 7201–7206 | 6 × 18 h | 36 |
| Locked holdout | 7301–7312 | 12 × 18 h | 72 |

The fixtures reuse Phase 2 time/sensor simulation and Phase 3 causal features. New independent seeds vary normal load and free space, short and long builds, backups, benign CPU oscillation and launch stalls, sustained and repeated true stalls, oscillating and sustained CPU contention, gradual and critical disk consumption, temporary consumption and cleanup, and sleep/gap patterns. Deep readings use 225, 300 or 420 s nominal cadence by run. Cache repeats and outages retain their quality metadata; each run has one selected synthetic volume. True RAM threshold events remain in the fixtures to test global safety. There are 24 CPU, 24 disk-capacity and 24 process events in the holdout, along with 12 RAM safety cases. These are seed variations of shared synthetic mechanisms, not separate real devices.

**Common accounting:** All methods share readiness/context guards, independent fresh observation counts, a time-based warning lifecycle, hysteresis, recovery, merge rules and flapping definition. All use the same labels and workload sessions. Matching assigns one same-subsystem episode per event using the first warning/critical notice in the actionable window. Silent OBSERVE does not count toward hits or interruptions. A warning before actionable onset remains a false advisory even if a later in-window critical upgrade earns event credit. A redundant warning during a labeled event is reported separately as a duplicate advisory under the inherited accounting. Missing readings cannot confirm or recover an episode. Associated process evidence on CPU notices is coincidence, not causal attribution.

**Signal families:** Fixed uses only guarded absolute levels. Past-only MAD uses Phase 3 median deviations with a predeclared subsystem-specific MAD floor. Sustained change uses valid directional slope/net change for CPU/disk and a valid sustained process-count mean for process responsiveness. The reduced hybrid combines the guarded fixed and sustained signals; it does not use ML or a new detector family.

**Selection:** Search six predeclared threshold/persistence pairs per method, for 24 points on development and validation. A point must pass both partitions on observed critical safety and ≤0.5 false interruptions/device-day and ≤5% backup/build nuisance. Among qualified points rank validation event recall first, then median/p95 delay, alert burden and grid order. Preserve over-budget points in the frontier and never switch a point after opening the holdout. If no useful comparator qualifies, do not claim superiority. A useful comparator needs ≥20% scoped recall as well as safety and budget. Row F1 is not a selection criterion.

**Value gate:** The hybrid must reach 50% overall scoped recall and beat every useful budget-qualified simpler point by at least 10 percentage points of recall or 20% lower common-hit median delay without more than five points of overall recall loss. No subsystem may lose more than 10 points; require ≥10 events per subsystem, ≥8 runs and a positive lower paired-bootstrap bound for the winning metric. The bootstrap resamples paired runs (1,000 seeded replicates); its intervals describe this synthetic experiment. A separately reported non-inferiority check would favor the simpler point if it were close enough in recall and delay.

**Lock:** The input manifest SHA-256 is `3bf1ebd1d6d641da5854c4b07a8c784bd2ebe8b8ffdbc048f6e0886e8278efff`. The hash binds each run's telemetry and labels, implementation sources, scope, generator definitions, grid, budget and matching rules. The four selected configurations were written before any holdout telemetry was opened; selection SHA-256 `4f1625155c415e0f7ee419b39451905cacce64b5d8be5032cd87e372d1d3a851`. The holdout was opened once; two independent state replays received the same validated Phase 3 rows, and labels opened after scoring.

## Alert budget frontier and frozen operating points

All selected points below had zero false interruptions and zero workload nuisance on validation, and passed safety and budget in both development partitions. The explicit frontier includes all 48 development/validation measurements; these are examples of the closest over-budget validation points, not candidates rerun on the holdout.

| Method | Frozen threshold / persistence | Best budget-qualified validation recall | Closest over-budget validation threshold / persistence | Over-budget recall and false/day |
|---|---:|---:|---:|---:|
| Fixed | 1.20 / 1.5× | 15/36 | 1.00 / 1.5× | 22/36; 1.82 |
| Past-only MAD | 1.35 / 2× | 11/36 | 0.85 / 1× | 10/36; 1.36 |
| Sustained change | 1.35 / 2× | 24/36 | 1.00 / 1.5× | 21/36; 1.82 |
| Reduced hybrid | 1.35 / 2× | 24/36 | 1.20 / 1.5× | 21/36; 1.82 |

## Head to head on the same locked C holdout

All methods saw **25,115 acquisition rows**, **8.809 monitored device-days**, and **36** legitimate workload sessions. The denominator caps adjacent acquisition gaps at twice cadence; sleep time is not counted as monitored. Every method stayed within the same cap, with exactly one unmatched critical interruption and zero false warnings. The unmatched interruption was a valid RAM threshold crossing outside a labeled actionable window; the offline evaluator counts it against nuisance without suppressing the deterministic alert.

| Method | Recall | Median / p95 delay | False interruptions/day | Workload nuisance | OBSERVE / interrupting episodes |
|---|---:|---:|---:|---:|---:|
| Fixed | 30/72 (41.7%) | 137 / 2,297 s | 0.114 | 0/36 | 159 / 55 |
| Past-only MAD | 26/72 (36.1%) | 273 / 2,298 s | 0.114 | 0/36 | 61 / 44 |
| Sustained change | 45/72 (62.5%) | 1,192 / 2,237 s | 0.114 | 0/36 | 181 / 64 |
| **Reduced hybrid** | **45/72 (62.5%)** | **265 / 2,174 s** | **0.114** | **0/36** | **186 / 70** |

Median/p95 in this table describe each method's detected events. The delay gate uses the common detected event set, not unrelated samples. The hybrid beat fixed by **20.8 recall points** (paired interval **15.3–26.4**) and MAD by **26.4 points** (**18.1–33.3**). The fixed comparator was faster on the 30 cases it shared with the hybrid; the hybrid passed against it on recall, not on speed. Hybrid and sustained detected the **same 45 events**; their common-hit medians were **265 s vs. 1,192 s**, a **77.8% reduction** (paired interval **71.0–79.5%**) with no recall loss. No budget-qualified simpler method was descriptively non-inferior on the joint recall/delay criteria.

| Subsystem | Fixed: hits; median / p95 | MAD: hits; median / p95 | Sustained: hits; median / p95 | Hybrid: hits; median / p95 |
|---|---|---|---|---|
| CPU/workload | 0/24; — | 7/24; 145 / 474 s | 14/24; 99 / 287 s | 14/24; 99 / 287 s |
| Disk capacity | 9/24; 2,214 / 2,374 s | 9/24; 2,214 / 2,374 s | 10/24; 1,859 / 2,342 s | 10/24; 1,859 / 2,342 s |
| Process responsiveness | 21/24; 112 / 475 s | 10/24; 257 / 350 s | 21/24; 1,276 / 1,345 s | 21/24; 265 / 1,019 s |

Disk-capacity sensitivity remains limited. Neither CPU nor disk improved over sustained change; the strongest incremental evidence is earlier process notices. Capacity exhaustion is not a prediction of physical drive failure.

### Safety, recovery and uncertainty

- **Safety:** 294 safety checks per method. All **19 observable critical cases** (12 RAM, 7 disk) notified on the first usable crossing, with **zero missed or delayed**. Five latent disk cases lacked a usable threshold crossing in the event and are reported separately, not treated as observed.
- **Lifecycle:** Fixed/MAD/sustained/hybrid median interrupting episode durations were 604/559/631/1,193 s; median matched recovery delays among closed cases were 179/202/152/209 s. Flaps within the defined 10-minute window were 12/0/0/0; duplicate advisories were 12/5/6/12. The hybrid's 12 duplicates were process notices inside already-detected events. They do not count as false under the frozen definition. **If those duplicates were charged as unwanted interruptions, the hybrid would have 13/8.809 ≈ 1.48/day.** Address this before any user-facing deployment; the sensitivity check does not change the preregistered gate.
- **Coverage and OBSERVE:** All methods had 797/75,345 eligible scoped subsystem rows without usable telemetry (1.06%). Hybrid OBSERVE was 186 silent episodes (21.1/device-day), separate from the 70 interrupting episodes. No silent state was counted as an actionable hit.
- **Uncertainty:** Hybrid recall 62.5% has a descriptive event-level Wilson interval of 51.0–72.8%, which ignores run clustering; paired differences above use the run bootstrap. Zero nuisance sessions among 36 still has a descriptive 95% Wilson upper bound of 9.6%. The point-estimate gate passed, but these small synthetic counts cannot establish live workload nuisance, calendar reliability, or hardware-failure odds.

## Acceptance, verification and next decision

| Gate | Result |
|---|---|
| Global deterministic safety | **Pass:** zero observable drops/delays. |
| Nuisance | **Pass:** all points at 0.114/day and 0/36 workload nuisance under the frozen definition. |
| Incremental detection value | **Pass:** recall gains against fixed/MAD and common-hit delay gain against sustained change. No subsystem lost recall. |
| Reproducibility | **Pass:** both replays produced decision SHA-256 `7af53a20293990e30f1b984ca6630e16b28e6b16d06e4a08b4c4a247c362d620`; gate/comparison outputs matched. |
| Performance | **Pass:** p95 **1.68 ms**, median **0.98 ms**, across 25,115 incremental Phase 3 feature updates plus selected scoring/lifecycle steps. Excludes collection, file I/O and evaluation; not a production SLA. |

Only request approval for reduced-scope offline research. Maintain global safety as a separate path. The synthetic results justify investigating future warning horizons, not starting them now. Duplicate process notices and slow/sometimes unobservable disk evidence remain constraints for any future release. Existing deterministic production monitoring stays untouched.

## Files, outputs and validation

- **Created:** this report; `ml/configs/phase45c_config.yaml`; `ml/configs/phase45b_reference_sha256.json`; `ml/src/ultron_health_ml/reduced/` (`__init__.py`, `datasets.py`, `policy.py`, `protocol.py`, `replay.py`); and `ml/tests/test_phase45c_reduced.py`. **Modified:** `ml/README.md` to document the C entry point. No dependencies added.
- **Reports:** `ml/artifacts/phase45c/verified-v1` contains `phase45c_config.yaml`, `scope_definition.json`, `selection_protocol.json`, `alert_budget_frontier.csv`, `comparator_operating_points.csv`, `candidate_operating_points.csv`, `event_evaluation.csv`, `episode_evaluation.csv`, `subsystem_evaluation.csv`, `workload_nuisance.csv`, `critical_safety_evaluation.csv`, `locked_holdout_manifest.json`, `reproducibility_manifest.json` and `acceptance_gates.json`, plus frozen selection and OBSERVE exports. Generated files are local and ignored.
- **Validation:** 198 Python tests passed (including scope, global safety, common lifecycle, budget search, locking/selection order, event matching, reproducibility and decision gates). The health fixture, command/agent/browser-tab/native-speech checks, Next.js production build, TypeScript and Git whitespace checks passed. Native speech used generated audio.
- **Git:** work remains local and uncommitted on `feature/phase-4-5-hybrid-detection`; `main` and `prod` are unchanged. No commit, push, merge, production integration or Phase 5 implementation.

From the repository root with the existing ML environment, reproduce in fresh directories:

```powershell
python -m ultron_health_ml.reduced.replay prepare --inputs ml/generated/phase45c/new-lock
python -m ultron_health_ml.reduced.replay evaluate --inputs ml/generated/phase45c/new-lock --locked-sha256 <hash-from-prepare> --reference-root . --output ml/artifacts/phase45c/new-report
```

The reference-root flag only verifies bytes. On a clean clone without the historical local exports, omit the flag; the report still records their expected hashes but must not claim it verified absent files.

The [frozen review snapshot](phase45c-reference/README.md) preserves the effective configuration, scope, selection protocol, operating points and acceptance results. Duplicate process-notice suppression is mandatory before any future user-facing deployment.
