# ULTRON Phase 4.5-B Nuisance Reduction and Acceptance Review

**REPEAT PHASE 4.5.** The validation-selected policy met both nuisance limits on the new synthetic holdout and retained 28/36 actionable events. It did not pass the predeclared detection gate: none of the required fixed, MAD, or sustained-change baselines operated within the same alert budget. The CPU, disk-capacity and process results justify narrower offline follow-up, not a production release or Phase 5.

## 1 Frozen references and scope

Phase 4 and 4.5-A were not rerun, rewritten, or used to select thresholds. The checksum manifests verified **90 Phase 4** and **102 Phase 4.5-A** files, including the A locked inputs, reports, configuration and source. Historical A sustained change: 14/24 events, 281 s median delay, 2.38 false episodes/day, 1/15 workload nuisance. Historical A full hybrid: 16/24, 423 s, 4.75/day, 2/15. The Phase 4 system-wide forest's 44 false episodes/day and 0/2 disk-exhaustion detections remain historical context, not direct comparisons to this B holdout.

All work is local on `feature/phase-4-5-hybrid-detection`. The Windows worker, SQLite, API, dashboard, controllers, and LLM code did not change. No deployment, Phase 5, live monitoring integration, or subsystem ML was performed.

## 2 Dataset and selection

| Partition | Independent seeds | Runs |
|---|---|---:|
| Benign development | 6101–6106 | 6 × 12 h |
| Validation | 6201–6206 | 6 × 12 h |
| Locked acceptance | 6301–6309 | 9 × 12 h |

The fixture generator retains Phase 2 clocks, cache and gaps and Phase 3's causal feature pipeline. New seed-driven overlays vary the growth and plateau of RAM in long builds, allocation and release in benign RAM-heavy sessions, backup memory, oscillating legitimate CPU, and short nonresponse during builds/launch/temporary workloads. It also varies device baselines, session order, sleep and sensor gaps, AC/power contexts, and fault trajectories. These are synthetic variants from shared mechanism families, not independent real computers. No approved real telemetry export was required or replayed.

Before generating inputs, the protocol defined actionable matching, false interruptions, budgets, objective, source hashes and variant roster. The locked manifest binds each file and seed; its SHA-256 is `1620b13f2f5927e12b43ba2a3f314949aabee706d64c2e2ad221c11578d04d62`. Development ranked policies for safety and nuisance before utility; validation selected from the top four plus ties. Utility rewards event recall and penalizes false interruptions, workload nuisance, duplicate notices, flapping and median delay. The exact coefficients and priority order are in `selection_objective.json`. Row F1 is never used for selection. The chosen `memory_process` policy detected **19/24** validation events with **0 false interruptions/day** and **0/15** nuisance sessions. Selection was written before opening the new holdout telemetry. Holdout inputs and labels were opened once; two independent policy-state replays received the same Phase 3 feature rows. Neither holdout scores nor results informed selection.

## 3 Advisory policy and safety

- **Safety:** The A pass is reused unchanged. Usable RAM ≥95% and disk free space <5% immediately open or upgrade a critical episode before advisory scoring. A usable cached crossing can trigger safety but cannot extend independent warning evidence. Statistical failure and missing optional signals cannot delay the pass. These are offline rules, not changes to production thresholds.
- **Memory:** Stable high RAM does not qualify as degradation. Require sustained directed growth, reduced headroom, distinct fresh samples, positive 300 s slope, fit ≥0.55 and net growth. Normal context requires at least 74% RAM and 1.6 percentage points growth; when CPU is ≥76% without corroborating nonresponse, require at least 83% RAM and 5 points growth. The warning needs at least 240 s of valid evidence; the critical threshold bypasses it. This is not blanket build suppression.
- **Process and CPU:** Process requires six independent confirmations spanning at least 330 s, with a higher nonresponse count during high CPU or corroboration from RAM/event activity. The selected policy uses the stronger process gate, not the fusion variant. The latter records coincident process nonresponse as supporting evidence on CPU episodes; it does not label it causal. Scoped variants also require CPU ≥86% with RAM or process corroboration. The fusion-only variant recorded five CPU associations; its holdout event and nuisance totals matched the stronger-process variant. It did not independently solve the memory nuisance problem.
- **Disk capacity:** Reuse A's elapsed evidence and signed decline on one fixed synthetic volume. Only distinct fresh measurements count; nothing claims SMART, wear, or physical failure. Slow cadence and recovery timing limit detection. The selected policy matched 2/3 gradual declines and 6/6 disk-critical actionable scenarios, with no disk-capacity false advisories. Critical threshold coverage is reported separately below.
- **Other subsystems:** Disk temperature is only a limited advisory candidate. Unplugged drain is experimental; it was active in the selected policy and contributed one false warning. The scoped variants record it only as OBSERVE. Battery capacity is coverage-only. The scoped variants test a conservative bounded event-count entry; the selected policy did not detect event bursts. No new collector fields or causal diagnoses were added.
- **Severity and recovery:** An OBSERVE record is silent and neither counts as an actionable hit nor a false interruption. A WARNING before actionable onset is a false interruption, even if it remains open at onset; a later in-window critical upgrade can earn separate event credit. Only WARNING/CRITICAL are user-interrupting. The evaluator also reports total episodes, warning openings and critical notifications (an upgrade is not a second episode). A hysteresis boundary and independent fresh recovery evidence control closure. Lifecycle variants shorten advisory recovery for fast sensors without modifying disk persistence or the safety path. Missing/stale readings never confirm or close a warning.

## 4 Locked holdout comparison

Nine runs provided **4.426 monitored device-days**, **12,604 rows**, **36 actionable events** and **21 workload sessions**. Delays are measured from actionable onset, among detections only. Variants below were locked before testing. The scoped results are ablations, not post-holdout selections.

| Policy | Events | False interruptions/day | Workload nuisance | Median / p95 delay (s) | OBSERVE |
|---|---:|---:|---:|---:|---:|
| Safety + sustained change | 25/36 | 2.71 | 5/21 | 235 / 1,658 | 0 |
| + lifecycle | 25/36 | 2.71 | 5/21 | 356 / 1,658 | 0 |
| Lifecycle without drain warnings | 23/36 | 2.49 | 5/21 | 828 / 1,669 | 127 |
| + stronger memory | 25/36 | 0.45 | 0/21 | 568 / 1,658 | 98 |
| + stronger process | 28/36 | 2.71 | 5/21 | 267 / 1,642 | 132 |
| + CPU/process fusion experiment | 28/36 | 2.71 | 5/21 | 267 / 1,642 | 132 |
| **Selected memory + process** | **28/36** | **0.45** | **0/21** | **545 / 1,642** | **103** |
| Scoped core | 28/36 | 0.23 | 0/21 | 550 / 1,642 | 63 |
| Scoped core + MAD | 28/36 | 0.23 | 0/21 | 550 / 1,642 | 70 |
| Scoped core + EWMA | 28/36 | 0.23 | 0/21 | 550 / 1,642 | 64 |
| Scoped core + CUSUM | 28/36 | 0.23 | 0/21 | 550 / 1,642 | 64 |
| Fixed budget-policy baseline | 15/36 | 1.13 | 2/21 | 1,507 / 2,032 | 0 |
| MAD budget-policy baseline | 16/36 | 9.49 | 15/21 | 1,486 / 2,028 | 0 |

Memory corroboration accounts for the observed decrease in nuisance in this ablation. The stronger process policy improves raw event recall, but does not by itself lower nuisance. None of the additional MAD/EWMA/CUSUM families improved the scoped core's holdout actionable detection, nuisance, or aggregate delay. More OBSERVE activity is not proof of value. Do not promote an additional family or switch to the lower-noise scoped variant after seeing the holdout.

For the selected policy, **2 false WARNING** and **0 false CRITICAL** episodes account for 0.45/day. The false warnings were one memory and one battery-drain case. The zero workload point estimate has a descriptive 95% Wilson upper bound of **15.5%**; this does not establish the live nuisance rate. Recall is **77.8%** (descriptive Wilson interval **61.9–88.3%**, ignoring run clustering). There were **103 silent observations** (23.3 episodes/device-day), **30 interrupting episodes**, **133 total**, 28 warning openings and 10 critical notifications including upgrades. Median interrupting episode duration was **978 s**; median recovery delay was **177 s** among detected and closed events. There were zero flaps and duplicate advisories. **186/92,437 eligible subsystem rows** lacked usable telemetry (0.201%). Secondary row precision/recall/F1 were **0.805/0.408/0.542**.

**Critical:** All **10 observable critical events** notified on the first usable crossing, with zero delayed or missed across 84 per-sample safety checks. Two other latent critical cases lacked a usable threshold crossing within the event and are reported as unobservable. An earlier disk warning does not prove a critical reading was observed.

## 5 Production candidate subsystem matrix

Statuses identify candidates for **further offline validation**, not permission to deploy. “Advisory only” and “experimental” are scope recommendations; any interruptions from these subsystems in the selected replay were included in the nuisance count.

| Subsystem | Status | Detected | False advisories | Median delay | Evidence quality and recommendation |
|---|---|---:|---:|---:|---|
| CPU/workload | Production candidate | 5/6 | 0 | 106 s | Corroborated pressure; replay across more legitimate sustained loads. |
| Memory | Experimental | 8/9 | 1 | 545 s | Useful growth signal; stress-test benign sustained allocations. |
| Disk capacity | Production candidate | 8/9 | 0 | 1,456 s | Capacity only; limited early sensitivity at current cadence. |
| Process responsiveness | Production candidate | 3/3 | 0 | 110 s | Small sample; validate prolonged stalls and overlap. |
| Event activity | Experimental | 0/3 | 0 | — | No useful selected-policy evidence; test conservative scoped variant separately. |
| Disk temperature | Advisory only | 2/3 | 0 | 1,178 s | Temperature only; not general disk health. |
| Battery drain | Experimental | 2/3 | 1 | 75 s | Unplugged context only; consider observe-only. |
| Battery capacity | Disabled | 0/0 | 0 | — | Coverage only, no capacity degradation decision. |

## 6 Gates, reproducibility and performance

| Gate | Result |
|---|---|
| Zero observed critical drops/delays | **Pass:** 0/10 dropped and 0/10 delayed. |
| ≤0.5 false interruptions/device-day and ≤5% workload nuisance | **Pass on this holdout:** 0.452/day, 0/21 sessions. |
| Actionable detection value | **Fail:** 28/36 exceeds the predeclared 50% floor, but no fixed/MAD/sustained comparator qualified at the required budget. Unmatched-budget raw gains do not establish superiority. |
| Determinism | **Pass:** both independent replays matched episode, severity, event matching and acceptance data, SHA-256 `64fd5d95cc7e56df6d955aa5aac70f8076e09559efaa259f617c92bef5f15bc4`. Timing excluded from digest. |
| Incremental p95 <100 ms | **Pass:** 1.37 ms p95 and 0.95 ms median for Phase 3 feature update plus selected policy step on local Windows. Excludes collection, IO and evaluation. |

A repeat should first establish useful **budget-qualified comparator operating points** on fresh development runs, then predeclare a reduced CPU/disk-capacity/process scope and confirm it on a new locked holdout. Keep the small event/session sample, shared synthetic mechanisms, timing noise, and missing real-workload coverage visible. These data do not support future failure probabilities or live alert rates. No ML was tested.

## 7 Reproduction, files and validation

From the repository root with the existing ML environment:

```powershell
python -m ultron_health_ml.nuisance.replay prepare --inputs ml/generated/phase45b/new-lock
python -m ultron_health_ml.nuisance.replay evaluate --inputs ml/generated/phase45b/new-lock --locked-sha256 <hash-from-prepare> --reference-root . --output ml/artifacts/phase45b/new-report
```

Use separate, empty B directories. The measured reports are in `ml/artifacts/phase45b/verified-v1`: `phase45b_config.yaml`, `selection_objective.json`, `candidate_ablation.csv`, `signal_ablation.csv`, `subsystem_ablation.csv`, `severity_evaluation.csv`, `event_evaluation.csv`, `episode_evaluation.csv`, `workload_nuisance.csv`, `subsystem_matrix.csv`, `locked_holdout_manifest.json`, `reproducibility_manifest.json`, and `acceptance_gates.json`. Additional files retain the frozen selection and OBSERVE episodes. The reference root is read-only and optional on clean clones without local frozen artifacts.

- **Created:** This report; `ml/configs/phase45b_config.yaml`; `ml/configs/phase45a_reference_sha256.json`; the `nuisance` package (`__init__.py`, `datasets.py`, `policy.py`, `protocol.py`, `replay.py`); and `ml/tests/test_phase45b_nuisance.py`.
- **Modified:** `ml/README.md` only, to link the B entry point. No dependencies added. Phase 4.5-A remains uncommitted on the same branch and unchanged apart from the README's new B section.
- **Validation:** 187 Python tests, ULTRON health fixture and command/agent/browser-tab/native-speech self-checks, Next.js production build, TypeScript and Git whitespace checks passed. Native speech used generated audio, not the live microphone. No commit, push, or merge was performed.
