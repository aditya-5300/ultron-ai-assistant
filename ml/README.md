# ULTRON ML foundation — Phase 2

This is an **offline synthetic-data module**, not a replacement for the Windows health worker. It does not connect to SQLite, the monitoring API, the dashboard, providers or controllers. ULTRON works without Python. There is no model, feature-engineering pipeline, training, FastAPI service, MLflow or RAG here.

**Synthetic telemetry is not evidence that ULTRON predicts real hardware failures.**

## Install and run (PowerShell, repository root)

Python 3.11+ is required. A dedicated virtual environment keeps tooling separate from the application:

```powershell
python -m venv ml/.venv
ml/.venv/Scripts/python.exe -m pip install -e './ml[test]'
ml/.venv/Scripts/python.exe -m pytest ml
ml/.venv/Scripts/python.exe -m ultron_health_ml.data_generation.generator --config ml/configs/synthetic.yaml --days 7 --interval 30 --seed 42 --output ml/generated/run-42
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

## Tests and Phase 3 implications

Tests cover seed reproducibility, all intervals/counts, irregular timing, skips/overruns, nulls/coverage, deep cache/staleness, schema bounds, chronology/as-of validation, all scenarios/labels/incidents, tendency/correlation/recovery, YAML/CLI and output separation/checksums.

Phase 3 should implement a CSV/window loader that reconstructs and validates every reading, rejects missing metadata/version drift, and selects only approved numeric metrics. Apply explicit quality/age masks before any training-only fitted imputation; never count cached deep values as new independent observations. Use past-only elapsed-time windows (including power/device context and gaps), temporal splits before fitting, and do not train on labels, post-incident metadata or global future statistics. Fit/apply transformations must share a versioned implementation, but no such transformation is built in Phase 2.

**Deferred existing-code issue:** Phase 1's CPU median/MAD retrospective history path can accept future rows. This remains tracked in `docs/PHASE-1-MONITORING-ARCHITECTURE.md` section H and is intentionally unchanged. Do not use that diagnostic function as a past-only feature pipeline. Its fix needs separate approval.

Known limitations: simplified physics/thresholds; no real device identity or calibrated workload distribution; finite sampling can miss short events; overlapping scenarios can saturate metrics; abrupt synthetic cleanup is explicit; no empirical failure labels, model accuracy, causal proof or real-world validation. This phase stops at generation and contract validation.
