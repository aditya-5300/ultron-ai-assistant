# ULTRON System Observatory

Open `http://127.0.0.1:3000/monitor` or **System monitor** in the assistant sidebar.
This is a separate, lightweight dashboard: it does not load the Three.js orb,
MediaPipe camera, microphone, or continuous cloud AI requests.

## Operating model

1. Select **Start monitoring**. A hidden, independent Node worker starts locally.
2. Default collection is every 30 seconds. Storage, battery capacity and relevant
   Windows events refresh every five minutes. Change polling/retention in Settings.
3. Review measured graphs, active signals, forward projections and backward causal
   hypotheses. Expand each issue for validation checks and its synthetic KB.
4. Corrective-action buttons ask for a single-use, 60-second approval. Changing the
   active power plan verifies the new GUID and offers an approved restore. The
   software never assumes that a command returning successfully repaired a problem.
5. **Stop monitoring** requests an orderly exit; a current Windows query may finish
   first (up to 45 seconds). Closing the dashboard or web server does **not** stop
   this worker. Sign-out, shutdown, worker termination, or OS policy can stop it.
   No Windows service, scheduled task, startup shortcut, or reboot autostart is
   installed. After a reboot, start it again from the dashboard.

The worker can also be run in the foreground with `npm run monitor`. Ctrl+C stops
that foreground worker; saved history remains. The API starts the same file as
a detached hidden process. An exclusive lock prevents duplicate collectors.
The dashboard explicitly reports stale samples and collector availability.

## What is actually measured

- Overall CPU usage from OS CPU-time deltas, normalized across logical processors.
  The first sample and the first poll after a long gap are unavailable, not zero.
- Physical RAM available/used. This is not virtual-memory commitment or pagefile
  pressure. Per-process working sets overlap and must not be added as total RAM.
- Process names, PID/start identity, CPU-time **deltas**, working sets and a limited
  window responsiveness flag. Windows Idle is excluded from process attribution.
  Only the top CPU/memory processes and nonresponding candidates are retained.
- Battery charge and AC/charging status, where exposed. Numeric full/design
  capacity and cycle counts come from a local Windows battery report. The full
  temporary report is removed after extraction; serial numbers are not retained.
  Multiple batteries are not combined into an invented single-pack drain model.
- Mounted fixed-drive capacity/free space; Windows physical-disk health and any
  available device temperature, wear and uncorrected-error counters. Some SSDs,
  storage drivers and non-admin contexts do not expose these counters. Null means
  unavailable. The agent does not elevate privileges to obtain them.
- At most 40 relevant recent records per System/Application log: provider, event
  ID, level, record ID and timestamp. Critical, error and warning levels are
  considered for a bounded ID allowlist. Raw messages, crash dumps and file paths
  from event data are not collected. Bounded log selection is not a complete audit.
- Active power-plan GUID and a known-plan label. Display brightness, radios,
  GPU counters, CPU package temperature, network behavior, malware and physical
  battery swelling are **not measured**.

No desktop screenshots, window titles, keystrokes, command lines, file contents,
browser history, account identifiers or credentials are collected by this feature.

Windows interfaces are described in Microsoft's documentation:
[CPU counters](https://learn.microsoft.com/en-us/windows/win32/perfctrs/collecting-performance-data),
[storage reliability](https://learn.microsoft.com/en-us/windows-hardware/drivers/storage/msft-storagereliabilitycounter),
[battery status](https://learn.microsoft.com/en-us/windows/win32/power/battery-status-str),
[event log queries](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.diagnostics/get-winevent).
The thresholds below are application triage heuristics, not manufacturer limits.

## Diagnostics, synthetic KB and local models

Ten versioned, explicitly synthetic KB articles contain symptoms, plausible
causes, validation steps, prevention and bounded corrective actions. They cover
CPU, RAM, storage capacity/reliability, battery wear/drain, power policy, application
instability, system events and baseline deviations. Articles are hand-authored
seed knowledge, **not** a trained vendor-verified diagnostic corpus.

The local inference pipeline runs without an API key:

- Sustained CPU/RAM rules require three consecutive supported polls; gaps and
  sleep/resume break the evidence window. One CPU spike is not an incident.
- Free-space warnings use 10%/5% thresholds; device-reported health, wear/errors
  and a generic temperature triage threshold are separate signals.
- Battery capacity below 80% of reported design capacity is a wear **signal**,
  not proof of failure. Calibration and charging policies remain alternatives.
- Unplugged battery decline needs at least eight uninterrupted minutes, six
  observations and adequate fitted consistency. It uses a conservative heuristic
  until enough historical unplugged windows support a median/MAD drain baseline.
- CPU anomalies use a robust median/MAD baseline with at least 60 comparable
  power-source observations. A deviation may simply mean a different workload.
- Runtime, RAM-growth and storage-space projections use gated least-squares
  trend models. Storage projections require at least 24 hours and 12 hourly
  observations; RAM growth needs at least ten minutes. All estimates disclose
  assumptions and fit information. Changes in work invalidate extrapolation.
- Restart/crash/storage event metadata supports hypotheses, never definitive
  root cause. For example, Kernel-Power 41 is not proof that a battery failed.

This is statistical anomaly detection plus diagnostic reasoning, **not a trained
hardware-failure classifier**. It does not estimate dates of physical battery
failure, fire probability, or disk remaining lifetime. Long-term improvements
come from representative local baselines; useful/false-positive feedback is
recorded for review, not used to silently retrain or disable safety gates.

Signals resolve only after three polls with the relevant sensor evidence and no
matching condition. Missing telemetry does not count as recovery. Resolved means
"no longer observed", not permanently fixed. The activity history records
detections, resolutions, permissions, configuration, feedback, actions and errors.

## Optional advisory AI

**Review data for AI analysis** prepares an exact, visible JSON preview. Approving
it sends that snapshot to the existing server-configured Gemini/OpenAI provider,
which may charge for usage. No automated periodic AI uploads are enabled.

The cloud payload includes numeric resource/battery/storage readings, signal
categories, model summaries and relevant synthetic KB. It excludes process names,
PIDs, desktop content, raw event messages, hostnames and serial numbers. The
advisory response is stored locally. It cannot invoke tools, change rules, approve
permissions, execute commands or generate executable remediation scripts.
AI output is a second opinion and must be validated, not treated as telemetry.

## Corrective-action boundary

Supported approved actions:

- Switch to the standard Balanced power plan, with actual active-plan verification.
- Restore the previously saved power plan, with fresh approval.
- Open Task Manager, power settings, storage settings or Event Viewer for manual
  investigation. Windows accepting a launch request is not proof of a repair.
- Queue a fresh read-only telemetry poll without changing system settings.

No process killing, file deletion, forced restart, automatic service restart,
driver installation, disk repair, registry edit, arbitrary script, antivirus
override or thermal/battery firmware change is supported. Safe collection and
analysis run automatically; potentially disruptive remediation remains reviewed.
Do not change an application's resource behavior based solely on a statistical
outlier or an AI-written causal hypothesis.

## Persistence and privacy

Data lives in `.ultron/health.sqlite` (SQLite WAL), separate from chat memory.
It is unencrypted, local and Git-ignored. A maximum of seven days / 20,160 samples,
2,000 events, 200 issue records and 30 AI reports is enforced. At faster polling,
the row cap can shorten the selected retention period. Graph queries are
downsampled server-side; inference uses dense recent data plus older hourly
observations to limit overhead. Deeper cached readings display their own timestamp.

Settings provides a separate **DELETE MONITORING HISTORY** confirmation. Stop
the collector first. Deletion removes local records, not exported files, provider
copies or filesystem backups. The JSON report includes process names and selected
event metadata: review it before sharing. Clearing assistant chat memory does not
clear monitoring history. Never expose these demo-authenticated routes publicly;
the API enforces same-origin localhost requests but is not multi-user authentication.

## Implementation

- `scripts/health-worker.cjs`: independent collector lifecycle, lock, heartbeat.
- `scripts/collect-health.ps1`: fixed, bounded read-only Windows queries.
- `lib/health/collector.cjs`: sampling, normalization, caching and unavailable states.
- `lib/health/core.cjs`: SQLite store, synthetic KB, diagnostic rules and local models.
- `lib/health/actions.ts`: fixed allowlisted, approval-gated Windows actions.
- `app/api/monitor/route.ts`: local control/data/report/advisory API.
- `components/MonitoringDashboard.tsx`: lightweight monitoring dashboard.

Node 24 is recommended for the built-in SQLite support already used in this app.
No new third-party dependency is required. If Windows execution policy prevents
the local collector script, the UI reports unavailable data; it does not bypass
policy or request administrator elevation automatically.
