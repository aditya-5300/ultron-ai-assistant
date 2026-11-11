# ULTRON Phase 4 — Offline Isolation Forest

**Recommendation: NO-GO for advancing this model to Phase 5 or live alerts.** The offline experiment is complete. The forest reduced backup false positives and detected memory leaks earlier than the fixed rules in these runs, but its F1 and recall trailed both baselines and its false alert episodes were much more frequent. Keep the existing monitoring path unchanged. Before reconsidering, use more independent runs and opt-in, locally retained telemetry across diverse normal workloads; evaluate episode-level workload suppression and a focused detector on fresh holdouts. Do not tune against these final test seeds.

Synthetic anomaly detection performance is not evidence of real hardware-failure prediction. [Isolation Forest](https://scikit-learn.org/1.8/modules/generated/sklearn.ensemble.IsolationForest.html) asks whether a feature pattern looks unusual relative to training; it does not identify a cause, forecast a threshold crossing, or estimate failure probability.

## Implementation and split

The existing Phase 2 generator and Phase 3 export were reused. The model sees only the ordered, numeric `model_columns` projection. Raw telemetry metadata, sample IDs, times, scenario labels, severity and incident times are excluded from the model matrix. Each run starts a fresh feature pipeline; no rolling state crosses runs. Phase 3 trailing windows, quality gates, and cached-observation deduplication are unchanged.

| Split | Independent seeds | Rows | Purpose |
|---|---|---:|---|
| Training | 101, 102 | 1,429 | Normal and legitimate backup/build activity; no anomalous injections |
| Validation | 201, 202 | 1,438 | Candidate and cutoff selection only |
| Test | 301, 302 | 1,433 | Final comparison after model and cutoff freeze |

Each run simulates six hours at nominal 30-second cadence with jitter and occasional skips. Sample IDs and telemetry hashes are checked for overlap; membership, timestamps, and hashes are persisted. The loader verifies the feature manifest, telemetry/feature file hashes, schema, fingerprint, column order, row IDs and UTC order. Training never reads its label sidecars. Validation opens labels after the first candidate is scored; each candidate is scored independently. The test split is not generated until model selection has finished, and test labels open after all methods have scored. No random row split or use of future records.

- Model version: `0.1.0`; telemetry schema: `1.0.0`; feature schema: `1.0.0`.
- Profile: `real_ultron`; configuration fingerprint: `d9c14284e9349439e07f632aaa91f077a2a8f662afb7ef9939b9666c8024ef47`.
- Dependencies added: scikit-learn and joblib. Benchmark used Python 3.13.12, scikit-learn 1.8.0, joblib 1.5.3, NumPy 2.4.4, Pandas 3.0.5, Pydantic 2.13.4 and PyYAML 6.0.3.

## Feature audit and validation selection

Training selected **229 of 373** columns. Drops: 67 all missing, 70 constant, 5 over 45% missing, 2 nonconstant with range at or below `1e-8`, 0 insufficient or explicitly excluded. Minimum observed count: 12. The mask is learned on the training split, not copied from the Phase 3 export audit. The model retains 19 varying available/fresh/cached/stale indicators; constant indicators are excluded like any other feature. Nulls use a fitted per-column training median, not generic zeros. This is a practical baseline, not proof that imputation is appropriate for every missingness pattern; correlated missingness and distribution shift may distort scores. Neither imputed values nor dropped columns indicate that a sensor is healthy. Missing/reordered required columns fail the signature check; a valid null within the ordered projection is imputed.

| Trees | Max samples | Max feature fraction | Scaled | Validation F1 | Selection objective |
|---:|---|---:|---|---:|---:|
| 100 | auto | 1.00 | No | 0.345 | 0.180 |
| **200** | **0.50** | **0.75** | **No** | **0.414** | **0.256** |
| 100 | auto | 1.00 | Yes | 0.345 | 0.165 |

All candidates use seed 42, one job, no bootstrap, and scikit-learn contamination `auto`; its default classification threshold is not used. Scaling, when tested, was fitted on median-imputed training values. It did not improve validation F1 or the objective for the matched 100-tree configuration. The ULTRON score is `-score_samples`: larger means more unusual. The selected decision is `score >= 0.5470035283212403`. Candidate cutoffs are 101 validation quantiles plus the no-alert boundary. Selection maximizes `F1 − (0.50 × workload FPR + 0.25 × overall FPR)` in profile scope, preferring lower false-positive burden and simpler preprocessing on equivalent results. No test score or label enters that procedure.

## Held-out results

Main scope: 1,240 test rows, including 329 labeled positives with at least one injected metric in the profile. The same exclusions apply to all methods: 193 positive rows belong solely to unavailable scenarios. Full-scope scores are also in `evaluation.json`; including all rows, forest recall is 20.1% and F1 is 0.289. This does not make network-only anomalies observable. Ranking metrics use only finite scores; 122 main-scope MAD rows had no usable robust Z and abstained from alerting. Threshold uses CPU ≥85%, memory ≥90%, or free disk ≤10% on usable current readings. MAD uses max absolute Phase 3 past-only robust Z ≥4 for two observations, resetting on missing score or long sample gaps; the existing retrospective runtime diagnostic is not used.

| Method | TP / FP / FN / TN | Precision | Recall | F1 | FPR | FNR | Specificity | Avg. precision | ROC-AUC |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Threshold | 137 / 102 / 192 / 809 | 0.573 | 0.416 | 0.482 | 0.112 | 0.584 | 0.888 | 0.505 | 0.777 |
| Past-only MAD | 178 / 136 / 151 / 775 | 0.567 | 0.541 | 0.554 | 0.149 | 0.459 | 0.851 | 0.616 | 0.782 |
| Isolation Forest | 105 / 99 / 224 / 812 | 0.515 | 0.319 | 0.394 | 0.109 | 0.681 | 0.891 | 0.462 | 0.687 |

The forest did not approach the reference targets of 0.80 precision and 0.75 recall. Its small reduction in row FPR versus the threshold rule did not translate to fewer interruptions: false flags were more fragmented.

| Method | True / false episodes | Mean / median duration | Alerts / false alerts per simulated day |
|---|---|---|---|
| Threshold | 10 / 3 | 524 / 394 s | 26.0 / 6.0 |
| Past-only MAD | 9 / 6 | 605 / 842 s | 30.0 / 12.0 |
| Isolation Forest | 13 / 22 | 147 / 31 s | 70.1 / 44.0 |

Episodes group consecutive flagged rows or merge interrupted flags at most 60 seconds apart. They do not cross a run, an excluded row, or a telemetry gap over 90 seconds. A true episode contains at least one flagged positive row and does not necessarily represent a unique incident. A single flagged observation has zero measured span. Rates normalize roughly 0.50 simulated day; they are not real-day forecasts.

### Legitimate workload false positives

| Method | Backup rows flagged | Episodes; mean / median span | Build rows flagged | Episodes; mean / median span |
|---|---|---|---|---|
| Threshold | 32/63 (50.8%) | 1; 927 / 927 s | 65/65 (100%) | 2; 958 / 958 s |
| Past-only MAD | 61/63 (96.8%) | 2; 899 / 899 s | 29/65 (44.6%) | 2; 416 / 416 s |
| Isolation Forest | 3/63 (4.8%) | 2; 14 / 14 s | 47/65 (72.3%) | 7; 178 / 119 s |

### Scenario coverage and timing

Counts are scenario instances with at least one alert out of two; negative-scenario alerts are unwanted.

| Scenario | Profile coverage | Threshold | MAD | Forest |
|---|---|---:|---:|---:|
| Normal | Full | 0 | 0 | 1 |
| CPU spike | Full | 2 | 2 | 1 |
| Sustained CPU pressure | Partial | 2 | 1 | 2 |
| Memory leak | Full | 2 | 1 | 2 |
| Disk exhaustion | Full | 2 | 1 | 0 |
| Network degradation | None | — | — | — |
| Response-time degradation | None | — | — | — |
| Error burst | None | — | — | — |
| Throughput surge | Partial | 0 | 2 | 0 |
| Multi-metric degradation | Partial | 2 | 2 | 2 |
| Backup | Partial | 1 | 2 | 1 |
| Compilation/build | Partial | 2 | 2 | 2 |

The forest first flagged memory leaks 59 s and 234 s after onset, 452 s and 328 s before their simulated incidents. Fixed rules alerted at those two incident timestamps. Sustained CPU was first flagged by the forest 15 s and 955 s after onset; the second was 605 s after its incident. The forest flagged one CPU spike after 49 s and both multi-metric cases after 232 s and 74 s. Full timestamps, fraction flagged, maxima and signed delays for every scenario and method are in `scenario_evaluation.csv`. The real profile has useful CPU/RAM/disk signal, but aggregate channels do not reliably distinguish legitimate builds from faults or cover application-level incidents. Partial observability does not guarantee detection.

| Method | Detected / eligible | Before / at / after / missed |
|---|---:|---|
| Threshold | 6 / 6 | 2 / 2 / 2 / 0 |
| Past-only MAD | 3 / 6 | 3 / 0 / 0 / 3 |
| Isolation Forest | 4 / 6 | 3 / 0 / 1 / 2 |

Eight additional simulated incidents had trigger metrics outside the profile; they are not counted as missed observable incidents. Detecting a simulated scenario before its trigger is not prediction lead time. Two held-out runs are insufficient to establish generalization; generator physics, workload diversity, sensor distribution and availability differ from real machines.

## Artifacts, reproducibility and benchmark

From the repository root, in an environment with the package installed (`python -m pip install -e "./ml[test]"`), run:

```powershell
python -m ultron_health_ml.models.training --config ml/configs/isolation_forest.yaml --profile real_ultron --datasets ml/generated/phase4-run --output ml/artifacts/isolation_forest/0.1.0
```

Use fresh empty output directories. Generated data and model artifacts are ignored by Git. The measured verification artifact is under `ml/artifacts/verified/isolation_forest/0.1.0`.

- `model.joblib`: fitted estimator, train-only mask, medians, optional scaler, ordered signature and cutoff.
- `metadata.json`, `selected_features.json`, `dropped_features.json`, `config_snapshot.yaml`, `checksums.json`: version, parameters, package versions, config snapshot, feature audit and SHA-256 checks of the six core payloads including evaluation. Hashes establish integrity, not provenance. Only load a trusted local artifact; [joblib deserialization is not safe for unknown files](https://scikit-learn.org/1.8/model_persistence.html).
- `evaluation.json`, `baseline_comparison.csv`, `scenario_evaluation.csv`, `episode_evaluation.csv`, `experiments.json`, `experiment_results.csv`, `splits.json`, `split_membership.csv`, `report.md`: evaluation and audit trail.

The internal `load_model_artifact(..., expected_signature, trusted=True)` checks missing/corrupted files, model and library compatibility, model/signature agreement, selected feature order, fitted statistics and cutoff. `predict_features` checks the Phase 3 projection before scoring. Held-out reload score delta was **0** at tolerance `1e-12`, with identical decisions; single-row and batch scores matched. The rerun produced byte-identical model payload, split membership, selected/dropped features and baseline/scenario/episode CSVs. Wall times and creation timestamps are not reproducibility targets.

| Measured on local Windows | Time or size |
|---|---:|
| Generate, engineer and load six runs | 20.40 s |
| Verify and load feature exports (included above) | 1.00 s |
| Selected preprocessing / estimator fit | 38 ms / 534 ms |
| Candidate fit and validation selection | 1.42 s |
| Baselines, label loading and final evaluation | 237 ms |
| Score 1,433 test rows in two batches | 127 ms |
| Single-row inference mean (five repeats) | 34.8 ms |
| Model payload / full artifact directory | 1,996,381 / 2,422,778 bytes |

## Validation and files

160 Python tests cover Phases 2–4, including training-only selection/imputation, validation-only cutoff, no training labels, artifact corruption and mismatch rejection, reload parity, and unchanged earlier predictions after future telemetry or evaluation labels change. The Windows health fixture, command, agent, browser-tab and native-speech self-checks passed. The Next.js production build, TypeScript `--noEmit`, Python compilation, and `git diff --check` passed. Native speech used generated audio, not the live microphone. The worker, SQLite, production API and dashboard paths were not changed; normal ULTRON operation still requires no Python.

**Created:**
- `ml/configs/isolation_forest.yaml`
- `ml/src/ultron_health_ml/models/__init__.py`, `config.py`, `datasets.py`, `preprocessing.py`, `isolation_forest.py`, `baselines.py`, `evaluation.py`, `artifact.py`, `training.py` (all in that package)
- `ml/tests/test_model_preprocessing.py`, `ml/tests/test_isolation_forest.py`, `ml/tests/test_model_artifact.py`, `ml/tests/test_model_evaluation.py`, `ml/tests/test_model_training.py`, `ml/tests/test_model_leakage.py`
- `docs/PHASE-4-ISOLATION-FOREST.md`

**Modified:** `.gitignore`, `ml/pyproject.toml`, `ml/README.md`.

No optional extended-profile benchmark, forecasting, FastAPI, MLflow, RAG, live integration, new permissions, or automatic model deployment. Phase 5 has not begun.
