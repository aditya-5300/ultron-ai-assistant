# Telemetry contract 1.0.0

Authoritative Pydantic definitions: `src/ultron_health_ml/contracts/telemetry.py`.
Telemetry version is literal `1.0.0`; incompatible versions and unknown fields are rejected. The package has its own generator version (`0.1.0`); this does not replace the telemetry schema version.

## Objects

`MetricReading`: `value: number|null`, `observation_time: UTC datetime|null`, `availability: available|unavailable`, `quality: fresh|cached|missing|stale`.

| Quality | Value/time | Availability | Numeric usability |
| --- | --- | --- | --- |
| fresh | Both present; observation time equals sample time | available | Usable |
| cached | Both present; observation time older than sample time | available | Potentially usable with age metadata |
| stale | Both present; observation time older than sample time | available | Null via `usable_value()`; diagnostic evidence only |
| missing | Both null | unavailable | Null, never zero |

`TelemetryRecord`: version, source (`synthetic|ultron`), source version, sample ID, sample UTC time, supported nominal interval, nullable positive elapsed seconds, nonnegative collection duration, nullable AC state, coarse power plan and a nonempty known-metric mapping. Values must be finite, in the registry bounds and numeric (not strings/booleans); count metrics must be integral. Source `ultron` disallows hypothetical application metrics.

`TelemetryWindow`: version, `window_start`, `window_end`, `as_of`, and 1–50,000 records. All dates require explicit UTC. Require `start <= end <= as_of`; every sample lies inside the inclusive window. Reject duplicate sample IDs, duplicate/out-of-order timestamps, mixed source/version and actual elapsed-gap mismatches. The first elapsed reading may describe an observation before the window or be null. Historical cached observations may predate the window but never their sample. Revalidate supplied instances as well as decoded JSON.

The generator conservatively limits potential scheduling ticks to 50,000, matching the inference-oriented window limit of 50,000 retained records. Large generation runs must be split into bounded runs/windows; no service or automatic streaming export is implemented yet.

## Evolution and safety

Future Node adapters must normalize UTC/units, preserve nulls/coverage, select stable device scope and send only the approved metric mapping. No process names, PIDs, command lines, raw event text or private documents belong in this contract. A real adapter is not part of Phase 2.

Source code and this versioned contract are tracked in Git. To inspect JSON Schema without modifying the application, import `TelemetryWindow` and call `TelemetryWindow.model_json_schema()`. Pydantic cross-field/time validators remain authoritative; JSON Schema alone cannot express all chronological invariants.

`scenarios.json` uses a separate evaluation schema. Labels, severity, incident dates and expected outcomes are never valid inference metrics or telemetry fields. Future ML feature schemas must be explicitly versioned and separate from this raw telemetry contract.
