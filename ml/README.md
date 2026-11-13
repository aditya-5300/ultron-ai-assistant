# ULTRON offline telemetry tooling

This is an **offline telemetry module**, not a replacement for the Windows health worker. It does not connect to SQLite, the monitoring API, the dashboard, providers or controllers. ULTRON works without Python. The module generates synthetic data and computes versioned, past-only features; there is no model, training, FastAPI service, MLflow or RAG.

**Synthetic telemetry is not evidence that ULTRON predicts real hardware failures.**

## Install and run (PowerShell, repository root)

Python 3.11+ is required. A dedicated virtual environment keeps tooling separate from the application:

```powershell
python -m venv ml/.venv
ml/.venv/Scripts/python.exe -m pip install -e './ml[test]'
ml/.venv/Scripts/python.exe -m pytest ml
ml/.venv/Scripts/python.exe -m ultron_health_ml.data_generation.generator --config ml/configs/synthetic.yaml --days 7 --interval 30 --seed 42 --output ml/generated/run-42
ml/.venv/Scripts/python.exe -m ultron_health_ml.features.engineering --input ml/generated/run-42 --config ml/configs/features.yaml --profile real_ultron --output ml/generated/features-run-42
```

The output directory must be empty. The generator never overwrites existing datasets. Omit `--config` to use validated built-in defaults; supplied CLI days/interval/seed override YAML values. Example small run: `--days 1 --interval 60`. Supported intervals are 15, 30, 60, 120 and 300 seconds. A size guard prevents more than 50,000 potential records per run; use smaller runs on memory-constrained machines.

Dependencies: NumPy (correlated simulation), Pandas (CSV inspection/output), Pydantic (validation), PyYAML (safe configuration loading), pytest (tests only). No scikit-learn/joblib/FastAPI/MLflow dependency is introduced. Dependency ranges are declared in `pyproject.toml`; manifests record versions/settings/checksums. Exact numerical reproduction requires the same dependency versions, seed and settings. It is not a cross-version reproducibility guarantee.

## Reuse and architecture boundaries

The Phase 1 report describes the real source of truth. The live path stays collection -> existing rules/MAD/trends -> transactional SQLite persistence -> dashboard. Python does not import Node code or read `.ultron/health.sqlite`. This phase reuses the meanings of current sensors and timing/coverage behavior, not the live storage interface.

Future Node integration should export bounded, sanitized, chronological windows. The current diagnostic `modelHistory()` and chart history are unsuitable training contracts because they downsample differently. Node remains the database owner. Model fitting and evaluation/promotion must remain separate from live inference in later phases.

## Real concepts versus synthetic-only metrics

The versioned registry is in `contracts/telemetry.py`:

| Group | Metrics | Meaning/limitations |
| --- | --- | --- |
| `REAL_ULTRON_METRICS` | `cpu_usage_pct`, `memory_usage_pct` | Aggregate CPU delta and physical RAM percentage. |
| Real | `disk_free_pct`, `disk_temperature_c` | One selected volume/device, deep data; not a machine-wide minimum or complete SMART model. |
| Real | `battery_pct`, `battery_capacity_pct` | Reported charge and derived full/design capacity percentage; unsupported values stay null. Capacity ratio is not capped at 100 because real reported/calibrated values can exceed design. |
| Real | `selected_process_nonresponding_count` | Aggregate of selected process records, not all installed/running applications. No names or PIDs. |
| Real | `relevant_event_count` | Bounded event metadata count, not raw logs. |
| Context | `ac_online`, `power_plan` | Nullable AC status and coarse power policy; no identifiers. |
| `SYNTHETIC_APPLICATION_METRICS` | `network_latency_ms`, `response_time_ms`, `error_rate`, `throughput_rps`, `disk_write_mbps` | Hypothetical application/network/workload metrics; **not collected by the current health worker**. Error rate is a 0–1 fraction, not a percentage. |

The real-concept list is intentionally a subset, not a copy of `HealthSample`. A future real adapter must select a stable volume/disk outside the model and define device changes; combining changing devices would invalidate comparisons. A record claiming source `ultron` cannot contain synthetic application metrics. These are contract concepts only: no real adapter was implemented.

## Contract, timestamps and data quality

Telemetry schema version: **1.0.0**. See `schemas/README.md` for detailed validation rules.

- Each record carries source/version, sample ID, UTC `sample_time`, nominal interval, actual elapsed time, simulated collection duration, context and per-metric readings.
- Each metric has a numeric-or-null value, UTC `observation_time` (or null), typed availability and `fresh`, `cached`, `missing` or `stale` quality. Unknown never becomes zero. Zero is valid measured data for some metrics, not a missing-value sentinel.
- A record may contain a supported subset of metrics; an omitted key is unknown/unsupported, not a zero. Future loaders must combine a source-appropriate allowlist with explicit missing-key handling.
- Fresh readings match the sample timestamp. Deep metrics refresh approximately every five minutes; cached readings retain their original timestamp/value. A configured refresh outage produces older cached readings, then stale readings after 660 seconds, or missing data if no prior reading exists.
- Stale raw values are retained as explicitly marked diagnostic evidence, unlike the current collector's exclusion of expired deep fields. `usable_value()` returns null for stale/missing readings. Later feature engineering must apply this quality gate and calculate age; reading raw CSV numeric columns alone is unsafe.
- Sampling uses a fixed UTC start, optional bounded jitter, dropped samples and overrun delays. Record elapsed time reflects actual retained timestamp gaps, not a fixed 30 seconds. No zero rows are inserted for skipped observations.
- `TelemetryWindow` enforces `window_start <= window_end <= as_of`, strictly increasing unique sample timestamps/IDs, one source/version, sensor timestamps no later than their sample, and elapsed-gap consistency. Samples outside the window are rejected, not silently filtered. Cached sensor timestamps may predate `window_start`; they may never be later than the sample/as-of boundary.
- Generated windows are bounded by the first configured time and the final retained sample; manifest bounds make the export explicit. No sensor/provider timestamps are inferred from labels.

## Synthetic dynamics and scenarios

Normal behavior combines time-of-day and shorter workload cycles with correlated noise. CPU, hypothetical throughput and response time share workload signals; network noise is correlated over time, memory responds more slowly, disk free space changes slowly, and charge follows a simplified AC/discharge cycle. This is a configurable illustrative process, not a calibrated physical or application simulator.

| Scenario | Injection | Incident truth |
| --- | --- | --- |
| `NORMAL` | No injection | None. |
| `CPU_SPIKE` | Short bounded CPU jump | Anomaly, no incident by construction. |
| `SUSTAINED_CPU_PRESSURE` | Elevated CPU and response time | CPU >= configured threshold continuously for the configured duration. |
| `MEMORY_LEAK` | Gradual RAM growth | RAM crosses configured percentage. |
| `DISK_EXHAUSTION` | Gradually falling free space | Free space crosses configured low threshold. |
| `NETWORK_DEGRADATION` | Increasing latency and response time | Hypothetical latency threshold crossed. |
| `RESPONSE_TIME_DEGRADATION` | Growing response time | Hypothetical response threshold crossed. |
| `ERROR_BURST` | Bounded error-rate jump | Hypothetical error threshold crossed. |
| `THROUGHPUT_SURGE` | Increased traffic/CPU | Unusual, but no incident by construction. |
| `MULTI_METRIC_DEGRADATION` | Correlated CPU/RAM/latency/response/errors | Hypothetical response threshold crossed; not five independent failure labels. |
| `BACKUP_WORKLOAD` | Temporary CPU/disk-write/response load | Legitimate unusual workload, no anomaly/incident label. |
| `COMPILATION_OR_BUILD_WORKLOAD` | Temporary high CPU/RAM/disk-write load | Legitimate unusual workload, no anomaly/incident label. |

YAML declares enabled scenarios with fractional start/duration and intensity. Default windows are disjoint; explicit overlapping scenarios add effects before physical bounds are applied. All injection sizes live in `scenarios.PROFILES`; incident thresholds live in configuration. Disable a scenario with `enabled: false`. Half-open windows include start and exclude end. Gradual scenarios use actual elapsed progress, not row count. Too-short windows can fall between observations or fail duration/incident gates: this is explicitly not an incident guarantee.

At scenario end the synthetic job/process is removed or the disk cleaned up. Memory and free space recovery is an explicit simulation reset, **not** a claim that leaks or exhausted disks self-repair. Legitimate workload recovery is bounded and does not create persistent incident labels. Temporary CPU/disk activity is not proof of a fault.

Quality events are separate from scenarios: `MISSING_SENSOR` nulls one metric in its window; `DEEP_COLLECTION_OUTAGE` prevents refresh of a deep metric while preserving available cache. Global random missingness also applies. No missingness changes the underlying latent metric stream. Latent incident truth can occur even while its sensor/sample is absent; it is not an observed detection or successful prediction.

## Dataset outputs and label isolation

- `telemetry.csv`: only telemetry/context/quality columns. For each numeric metric, `__observation_time`, `__availability` and `__quality` columns accompany the value. Null numeric values/times use empty CSV cells; loaders must preserve them as missing, never `fillna(0)`.
- `scenarios.json`: evaluation-only scenario ID/type/start/end, optional incident time, severity, expected behavior, incident definition/intensity, quality events and per-retained-sample scenario/anomaly/incident labels keyed by sample ID.
- `manifest.json`: full generator settings, dependency/generator/schema versions, window bounds/count, file SHA-256 checksums and synthetic-data disclaimer. It is provenance, not a feature file.

Labels never appear in `TelemetryRecord`, `TelemetryWindow` or `DECLARED_MODEL_INPUT_FIELDS`. The metric allowlist is only a candidate input registry, **not** a trained model feature schema. The scenario/manifest files must never be concatenated into training rows. Incident definitions are evaluation heuristics, not labeled real hardware failures. No large datasets are tracked; `ml/generated/`, virtual environments and caches are ignored.

## Phase 3 feature engineering

**Feature engineering does not itself predict failures.** The same `iter_features(window, config)` function accepts validated Phase 2 `TelemetryWindow` objects for offline training, evaluation and future inference. The CLI loads checksummed `telemetry.csv` and `manifest.json` and writes `features.csv` and `feature_manifest.json`. Loading `scenarios.json` requires the separate evaluation-only loader; feature generation never opens it.

Telemetry and feature schema versions are independent; both are currently **1.0.0**. The manifest stores the ordered model columns, feature units, source/version, generator version, input/output checksums, resolved configuration and quality report. The configuration fingerprint is SHA-256 of canonical sorted JSON containing the feature version and settings, including the resolved metric order. IDs, timestamps, source and fingerprint metadata are not model columns. Use `row.model_features()` or the manifest's explicit `model_columns`, not all CSV columns.

- `real_ultron` uses only eight real-supported channels: aggregate CPU, physical RAM, selected-volume free space, battery charge/capacity ratio, selected-disk temperature, selected-process nonresponding count and bounded relevant event count. Synthetic records can exercise this profile without hypothetical application inputs.
- `synthetic_extended` adds network latency, response time, error-rate fraction, request throughput and simulated disk-write MB/s. It rejects a real ULTRON input window. A future real adapter must supply stable device selection and validated timestamps; it is not implemented here.
- A profile can select a smaller ordered subset of its allowed metrics in YAML. Defaults and exact column names are described in the [Phase 3 report](../docs/PHASE-3-FEATURE-ENGINEERING.md). The manifest lists each generated column explicitly.

### Windows and quality

Each row's `as_of` is its acquisition time `T`. A trailing window of length `W` uses distinct usable observation timestamps in inclusive `[T − W, T]` from the current continuity segment, and only records acquired by `T`. Default windows are 120, 300, 900 and 3,600 seconds. There is no row-count approximation, interpolation, future fill or statistical fit. A valid rolling window needs at least three observations and observed first-to-last span at least 50% of its duration by default. This span ratio is an evidence gate, not an estimate of sensor uptime. Counts, coverage and largest internal gap are available as per-row diagnostics, not model columns.

Current numeric features preserve nulls for missing, omitted and stale readings. Binary available/fresh/cached/stale flags and age in seconds are explicit model inputs: availability means a raw reading was supplied, so stale readings may be available but not usable. Age is computed from acquisition and original observation time, never the wall clock. Cached data at or past the configured age limit is treated as stale. Reused deep observations count only once; inconsistent values at the same timestamp or backward measurement times are rejected.

An acquisition gap above `gap_factor × min(previous cadence, current cadence)` resets history. An observation gap above the same factor times the metric's expected cadence also resets that metric (deep cadence defaults to 300 seconds). Unusable data breaks that metric's continuity. Cached readings observed before a reset cannot restore the old history; an unexpired cache can still provide a current value. Changes to AC state or power plan reset CPU and battery-charge histories and CPU-based pairs; other metrics retain their independent histories. AC/plan and change/gap flags are metadata.

### Statistics and relationships

- Valid windows emit mean, median, population standard deviation, minimum and maximum. Dynamic metrics also emit signed delta, absolute change, relative percent change, endpoint rate per hour, least-squares slope per hour, and trend R². Count channels and battery capacity omit short-term trend/change and movement features.
- Rates and slopes use actual elapsed time. Slopes need at least three distinct observations and 60 seconds of span by default. A valid constant channel has zero slope but no R². Relative changes with a zero/near-zero reference and non-finite calculated values are null. There is no learned clipping or imputation.
- The baseline uses the same metric's contiguous history strictly **before** the current measurement timestamp, inside a configured lookback (default 60 minutes). The median absolute deviation is `median(abs(history − median))`. Robust Z is `(current − median) / (1.4826 × MAD + epsilon)`, and is null when MAD is at or below epsilon. Insufficient history or an unusable current reading yields null baseline features.
- Consecutive movements count the latest same-direction run of unique observations inside the longest configured window; ties end the run. A threshold margin is positive on the configured safe side (default RAM 90%, free space 10%). Neither a margin nor a trend is an incident decision.
- `cpu_memory_pressure` is the smaller of usable CPU and RAM percentages: a high value requires both to be high. The extended profile also measures Pearson correlation for CPU/response, latency/response and throughput/error over matched fresh observations in a default 900-second contiguous window. Missing pairs break their history; constant inputs yield null correlation. These are descriptive relationships, not causal diagnoses.

### Parity and evaluation

Run training/evaluation/inference with the same feature profile, fingerprint, units and window evidence. Replay enough preceding acquisitions for the longest rolling/baseline/correlation horizon, plus cache age and preceding continuity context. Truncated replay can legitimately produce fewer valid features; never fill the resulting gaps with zero. Future records outside a requested `as_of` are rejected at the telemetry boundary.

Quality summaries describe the completed export (missingness, ranges, constant/near-constant fields, invalid windows, breaks and deep duplicates); none of those global summaries feed back into row features. Do not use feature or label metadata as inputs. Preserve nulls, split chronologically before fitting any scaler/imputer, and fit only on training data. Evaluation may join label sidecars by sample ID after feature generation. Keep simulations from the same scenario/run together when measuring holdout behavior and retain legitimate workload negatives. No existing diagnostic CPU retrospective baseline was changed or used by the feature pipeline.

## Phase 4 offline anomaly detection

The `models` package trains Isolation Forest on the approved Phase 3 projection and compares it with fixed thresholds and past-only MAD on independent synthetic holdouts. It does not connect to the worker, database, or dashboard. See the [Phase 4 report](../docs/PHASE-4-ISOLATION-FOREST.md) for the reproducible command, model artifact contract and measured results.

## Phase 4.5 offline hybrid detection

The `hybrid.replay` CLI prepares a separate hash-locked synthetic holdout, freezes development choices, and compares subsystem alerts with the fixed and past-only MAD baselines. The [Phase 4.5 report](../docs/PHASE-4-5-HYBRID-DETECTION.md) gives the commands, results, and acceptance decision. Phase 4 references are read-only, and no detector is connected to live monitoring.

## Phase 4.5-B offline nuisance reduction

The `nuisance.replay` CLI uses new locked inputs to compare stricter warning policies and silent observations while leaving the Phase 4.5-A safety pass and reference files unchanged. See the [Phase 4.5-B report](../docs/PHASE-4-5-B-NUISANCE-REDUCTION.md) for the locked evaluation, acceptance gates and reproduction steps.

## Phase 4.5-C reduced scope validation

The `reduced.replay` CLI selects budget-qualified fixed, MAD, sustained-change and hybrid operating points before opening a new locked synthetic holdout. See the [Phase 4.5-C report](../docs/PHASE-4-5-C-REDUCED-SCOPE-VALIDATION.md) for the fair comparison, limits and reproduction steps. Prior references and production monitoring remain unchanged.

## Phase 5 offline forecasting research

The `forecast.replay` CLI freezes actionable targets and compares simple forecast baselines before opening a new locked synthetic holdout. See the [Phase 5 report](../docs/PHASE-5-EARLY-WARNING-FORECASTING.md) for the process-only recommendation, evidence limits, and reproduction steps. No forecast is connected to live monitoring.

## Phase 6 local inference contract

The optional `service` extra provides a token-authenticated loopback API for standalone, bounded replay. See the [Phase 6 report](../docs/PHASE-6-LOCAL-INFERENCE-SERVICE.md) for the launcher, contract, limitations and benchmarks. There is no production monitoring adapter.

## Tests and limitations

Tests cover seed reproducibility, all intervals/counts, irregular timing, skips/overruns, nulls/coverage, deep cache/staleness, schema bounds, chronology/as-of validation, all scenarios/labels/incidents, tendency/correlation/recovery, YAML/CLI and output separation/checksums.

Feature tests cover label isolation, future-prefix invariance, loader integrity, irregular intervals, cache deduplication, null handling, elapsed-time trends and context boundaries.

**Deferred existing-code issue:** Phase 1's CPU median/MAD retrospective history path can accept future rows. This remains tracked in `docs/PHASE-1-MONITORING-ARCHITECTURE.md` section H and is intentionally unchanged. Do not use that diagnostic function as a past-only feature pipeline. Its fix needs separate approval.

Known limitations: simplified physics/thresholds; no real device identity or calibrated workload distribution; finite sampling can miss short events; overlapping scenarios can saturate metrics; abrupt synthetic cleanup is explicit. Short windows on deep sensors are often invalid by design. Window statistics weight each distinct observation equally, not by duration. Phase 4 metrics are synthetic only; there are no empirical failure labels, causal proof or real-world predictive validation.
