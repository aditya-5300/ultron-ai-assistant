# ULTRON Phase 3 feature engineering

**Status:** Implemented locally on `feature/phase-3-feature-engineering`, based on approved `prod` at `1c3b63b`. No commit, push or merge has been performed. No Phase 4 work started.

**Feature engineering does not itself predict failures.** This phase provides one reusable, deterministic, past-only feature transform. There is no model training, Isolation Forest, preprocessing fit, FastAPI, MLflow, SQLite change, RAG, dashboard change or live integration.

## Source contract and files

Phase 1 architecture and Phase 2 exports were inspected before editing. No Phase 2 contract defect required a change. The loader reuses its UTC/as-of and per-metric quality rules, verifies the telemetry CSV SHA-256 and record count, and reconstructs `TelemetryWindow`. No production runtime files or existing Phase 2 Python modules were edited.

| Created file | Purpose |
| --- | --- |
| `ml/configs/features.yaml` | Default windows, profile, evidence gates, continuity and thresholds. |
| `ml/src/ultron_health_ml/features/__init__.py` | Offline feature package namespace. |
| `ml/src/ultron_health_ml/features/config.py` | Profile allowlists, validated settings and configuration fingerprint. |
| `ml/src/ultron_health_ml/features/schema.py` | Ordered columns/units, feature row and separate quality metadata. |
| `ml/src/ultron_health_ml/features/validation.py` | Verified CSV/window reconstruction and explicit evaluation-only label loader. |
| `ml/src/ultron_health_ml/features/windows.py` | Actual timestamp bounds, bounded history and evidence diagnostics. |
| `ml/src/ultron_health_ml/features/statistics.py` | Descriptive statistics, MAD, rates, slopes, movements and correlations. |
| `ml/src/ultron_health_ml/features/engineering.py` | Shared iterator, continuity and deduplication, CLI. |
| `ml/src/ultron_health_ml/features/export.py` | Streamed CSV, checksummed manifest and post-export quality summary. |
| `ml/tests/feature_helpers.py` | Shared deterministic telemetry fixtures. |
| `ml/tests/test_feature_validation.py` | Input integrity, labels, version and settings validation. |
| `ml/tests/test_feature_windows.py` | Cadence, jitter, skipped polls, overruns, gaps and cache behavior. |
| `ml/tests/test_feature_statistics.py` | Mathematical and degenerate cases. |
| `ml/tests/test_feature_engineering.py` | Nulls, stale data, profiles, cross-metrics, export and CLI. |
| `ml/tests/test_feature_leakage.py` | Future-prefix, as-of, delayed acquisition, baseline and label isolation. |
| `docs/PHASE-3-FEATURE-ENGINEERING.md` | This completion and validation report. |

**Modified:** `ml/README.md`. No removed files, no added dependencies.

## Versions and model input boundary

Telemetry schema **1.0.0** is unchanged. Feature schema **1.0.0** is a separate version. Each row has sample ID/time, row-level as-of, source/version, dataset generator version where supplied, telemetry/feature versions, profile, configuration fingerprint and context/gap metadata. The row-level as-of is always its own acquisition timestamp, independent of the full export's final timestamp.

`FeatureRow.model_features()` returns only the profile-derived ordered numeric/null fields. IDs, timestamps, versions, context, label fields and dataset-wide quality analysis do not appear in that projection. CSV metadata is retained for audit and joins; the manifest's ordered `model_columns` is the selection contract. Unknown or label-like CSV headers are rejected even if the telemetry checksum is correct. The pipeline neither opens nor depends on `scenarios.json`; evaluation explicitly calls `load_evaluation_labels()` and later joins by sample ID.

The fingerprint is SHA-256 of canonical JSON containing the feature schema version, resolved metric order and settings. Reordering keys leaves the hash unchanged. Changing the profile, metric order, duration, evidence gate, gap/cache setting, baseline or threshold changes it. Data and output checksums are separate provenance, not model inputs.

## Complete default feature inventories

**Real ULTRON ready profile (373 model columns).** The eight prefixes below correspond to known telemetry concepts, not a new collector.

| Metric prefix | Raw unit | Dynamic changes and slopes |
| --- | --- | --- |
| `cpu_usage_pct` | % | Yes |
| `memory_usage_pct` | % | Yes |
| `disk_free_pct` | % | Yes |
| `battery_pct` | % | Yes |
| `battery_capacity_pct` | % | No |
| `disk_temperature_c` | C | Yes |
| `selected_process_nonresponding_count` | count | No |
| `relevant_event_count` | count | No |

For every prefix `m`, this is the exact expansion of its column names:

- `m__{current,available,fresh,cached,stale,observation_age_seconds}`
- `m__{baseline_median,baseline_mad,baseline_deviation,robust_z}`
- `m__{mean,median,std,min,max}_{120s,300s,900s,3600s}`
- Dynamic metrics additionally: `m__{consecutive_increases,consecutive_decreases}` and `m__{delta,abs_change,pct_change,rate_per_hour,slope_per_hour,trend_r2}_{120s,300s,900s,3600s}`.
- Threshold margins: `memory_usage_pct__threshold_margin` and `disk_free_pct__threshold_margin`. Cross-metric: `cpu_memory_pressure`.

**Synthetic extended profile (656 model columns).** Includes all 373 columns above, plus the full dynamic expansion for each of `network_latency_ms`, `response_time_ms`, `error_rate`, `throughput_rps` and `disk_write_mbps`. Units respectively: ms, ms, fraction, requests/s, MB/s. Also adds `cpu_response_correlation_900s`, `latency_response_correlation_900s`, and `throughput_error_correlation_900s`. None of those hypothetical application measurements is collected by the current Windows worker. The extended profile refuses a source of `ultron`.

The manifest enumerates each resolved name and unit, including when a metric subset or different duration is configured.

## Window and statistic definitions

- **Time:** Window `[T − W, T]` is inclusive and uses actual UTC observation times from records acquired by `T`. Defaults: 2, 5, 15 and 60 minutes. No row-count approximation. Internally integer microsecond timestamps preserve boundaries.
- **Evidence:** Default at least three observations and observed span at least half the requested duration. Per-window diagnostics expose validity, count, span/duration, and largest gap between retained observations. Invalid statistics are null. Span coverage is not estimated sensor uptime.
- **Missing and stale:** Unavailable, omitted and stale values remain null. Binary availability indicates a raw sensor reading, not usable health. Usable cached/fresh, stale and observation-age indicators are explicit inputs. A cached reading reaching the configured age limit (default 660 seconds) is stale. No wall-clock-derived age or fill.
- **Deep readings:** Deduplicate by original sensor timestamp. Identical cache repetitions do not add evidence or movement. Reject inconsistent values sharing a timestamp and backward observations. Unexpired cache may give a current value after a break but cannot restore pre-break history.
- **Continuity:** Acquisition gaps greater than 2.5 times the smaller adjacent nominal cadence reset all histories. Sensor gaps above the factor times expected cadence reset that metric; deep expected cadence is 300 seconds. Unusable readings reset that metric's history. AC/power-plan changes (including unknown transitions) split CPU and battery-charge histories and the CPU/response pair. This prevents mixing incompatible contexts; other metrics are unchanged.
- **Rolling:** Mean, median, population standard deviation, min and max weight each unique observation equally. Signed delta is last minus first, absolute change its magnitude, and relative percent change uses the absolute first value. Near-zero denominator returns null.
- **Trend:** Endpoint rate and OLS slope use actual hours elapsed. Units are percentage points/hour for percent levels, otherwise raw unit/hour. Default slope requires three observations and at least 60 seconds of span on top of normal evidence gates. A supported constant slope can be zero; undefined R² is null. No incident classification.
- **Baseline:** In the default 60-minute lookback, require five prior contiguous measurements and 50% span coverage. Only measurements strictly before the current measurement timestamp qualify, including when the current measurement is cached. MAD is median absolute deviation from median. Emit median, MAD, signed deviation and robust Z. Robust Z uses denominator `1.4826 × MAD + epsilon`; return null when MAD is at or below epsilon or evidence/current value is unavailable.
- **Movement:** Length of latest same-direction run inside the longest configured window; ties stop it. One or no observations produces null. Missing/gaps/cache rules apply before counting.
- **Proximity:** Signed margin on the safe side, in percentage points for default RAM/free-space thresholds (90% and 10%). These are engineering features, not failure guarantees.
- **Cross-metrics:** `cpu_memory_pressure` is the smaller of usable CPU and RAM percentages, null if either is missing. Extended correlations are Pearson r across matched fresh readings with the usual contiguous evidence gate; missing pairs break history, and constant pairs have no r. These relationships are descriptive, not causal.

## Leakage tests and shared use

`iter_features(window, config)` is the shared path. Training and evaluation can use it on verified exports; future inference can feed the same bounded Phase 2 contract without CSV. Replay sufficient preceding data for the longest enabled horizon, cache age and continuity context. A shorter replay may produce less evidence, not different fitting logic. Use the same fingerprint and ordered columns. The pipeline prunes past history by elapsed time and selects window bounds by binary search, then processes only retained windows. CSV rows and quality aggregates are streamed rather than retaining the entire output matrix.

| Required test | Result |
| --- | --- |
| A: Change every raw observation after T | All feature rows through T unchanged. |
| B: Append severe future anomaly | Prefix and bounded replay match. |
| C: Modify future labels and update their checksum | Features unchanged; explicit evaluator sees changed labels. |
| D: Remove scenario sidecar | CLI and normal generation still work. |
| E: Attempt to contaminate values/headers with labels or metadata | Invalid raw headers rejected; model projection excludes extras. |
| F: Baseline at T excludes future/current values | Verified exact median/MAD and cached-current exclusion. |

Additional tests reject records outside as-of, prevent backfilling a deep reading before it was acquired, reject conflicting cache data, and cover all-missing/partial windows, 15/30/60-second intervals, jitter, skips, overruns, gaps, constant values and context boundaries.

## Seven day smoke test and quality

Input: supplied Phase 2 YAML, seed 42, nominal 30-second interval, seven days with jitter, skips, overruns and simulated missing/stale readings. **19,762** retained input rows (20,160 would be exact fixed cadence without losses). Generated data and full manifests are local and ignored by Git.

| Profile | Rows × features | Feature export wall / CPU | Full CLI | Invalid windows | Constant / near-constant / all missing |
| --- | --- | --- | --- | --- | --- |
| Real ULTRON | 19,762 × 373 | 76.86 s / 75.03 s | 87.56 s | 312,714 | 67 / 0 / 67 |
| Synthetic extended | 19,762 × 656 | 166.46 s / 161.59 s | 177.60 s | 408,301 | 82 / 0 / 67 |

Feature export timing includes transformation, CSV and quality summary, not the earlier load; full CLI also includes startup and input validation. This is a local Windows benchmark under normal validation activity, not an SLA. Profiling identified repeated median and trend work; the final pipeline reuses unchanged deep window results and avoids redundant reductions. Both optimized exports matched their earlier results within numeric tolerance (relative `1e-10`, absolute `1e-9`) with identical null masks.

Real-profile summary: 70,814 repeated deep readings ignored, 102 sampling gaps, 27 context changes and 306 metric continuity resets from unusable data. Current CPU is 1.25% missing (13.37–100%), RAM 0.19% (45.49–100%), and disk free 2.66% (0–67%). The observed disk-free zero is simulated exhaustion, not an imputed null. Full per-feature observed counts, missing percentages, extrema, invalid windows by group and cache counts are in `feature_manifest.json`; all-missing and constant columns remain in the schema. These are dataset-quality results, not model performance.

Extended-profile summary: the same 70,814 deep duplicates, 102 sampling gaps and 27 context changes; 495 metric and 231 matched-pair resets from unusable data. Current simulated latency is 0.17% missing (15.58–282.79 ms), and response time is 0.19% missing (105.31–1,348.72 ms).

Outputs: `ml/generated/phase3-seven-day-input/`, `ml/generated/phase3-seven-day-final-real_ultron/`, `ml/generated/phase3-seven-day-final-synthetic_extended/`.

## Existing ULTRON validation

- Full Python suite: **143 passed** (72 Phase 2, 71 new), plus Python compile checks.
- Existing external health fixture passed. Only its import path was remapped in memory to the relocated checkout; the fixture and repository were not edited by that run.
- Registered command, agent, browser-tab and native-speech checks passed. These are fixtures/generated speech, not live microphone or system-action tests.
- Production Next.js build and separate TypeScript noEmit passed. The rebuilt ULTRON served `/assistant` with HTTP 200 and retained the desktop voice dropdown.
- Git whitespace check passed. Production app/components/lib/scripts, SQLite and Node dependency manifests are unchanged; the app still requires no Python.

## Limitations and recommendations for Phase 4

Short windows on deep sensors often lack independent data; conservative resets can also make long windows sparse. Keep the quality report visible rather than silently dropping or imputing columns. Current correlation and feature thresholds are descriptive; synthetic faults and coverage do not prove real failure prediction. Stable volume/device selection and enough history must be supplied by a future adapter. Phase 1's documented CPU retrospective baseline leakage risk remains unchanged; the feature pipeline does not use that implementation.

For Phase 4, choose a profile that matches available sensors. Split by time/run before learned preprocessing, fit only on the training partition, version the selected column list and config fingerprint, and audit sparse, constant and context-dependent features before training. A later training policy may drop fields based on training-only evidence. Evaluate on held-out future runs, including legitimate backup/build workloads; keep evaluation labels outside the matrix. Distinguish anomaly ranking from incidents and physical failure forecasts. None of this training work was started here.

**Git review:** Only this phase's local files changed. `main` and `prod` remain unchanged. Commit/push and the exact commit message require fresh confirmation; integration into `prod` requires separate approval.
