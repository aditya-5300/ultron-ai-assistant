const { DatabaseSync } = process.getBuiltinModule('node:sqlite');
const fs = require('node:fs');
const path = require('node:path');
const { randomUUID } = require('node:crypto');
const { enrichArticle } = require('./evidence.cjs');

// These articles suggest checks and possible causes; they are not confirmed hardware diagnoses.
const KB = [
  { id: 'cpu-load', title: 'Sustained CPU load', symptoms: ['CPU above 85% for three consecutive samples'], causes: ['Expected heavy workload', 'Background application or update', 'Stalled application or driver'], checks: ['Compare process CPU deltas, not lifetime CPU time.', 'Wait for known builds, scans and updates to finish.', 'Check Task Manager before closing anything.'], prevention: ['Review startup apps and background work.'], actions: ['openTaskManager'], sources: ['https://learn.microsoft.com/en-us/windows/win32/perfctrs/collecting-performance-data'] },
  { id: 'memory-pressure', title: 'Memory pressure or sustained growth', symptoms: ['Available RAM low', 'RAM use above 90%', 'Consistent upward trend'], causes: ['Many applications or browser tabs', 'Growing application working set', 'Workload needs more memory'], checks: ['Review memory consumers in Task Manager.', 'Save work before closing an application.', 'A rising working set alone does not prove a memory leak.'], prevention: ['Limit unnecessary startup apps.', 'Plan capacity for demanding workloads.'], actions: ['openTaskManager'], sources: [] },
  { id: 'disk-space', title: 'Low free space', symptoms: ['Less than 10% free space on a mounted fixed drive'], causes: ['Large downloads or build artifacts', 'Application data growth', 'Normal OS update storage'], checks: ['Use Windows Storage settings to review categories.', 'Back up and review files before deleting.', 'Do not erase system folders automatically.'], prevention: ['Review storage usage periodically.'], actions: ['openStorageSettings'], sources: [] },
  { id: 'storage-health', title: 'Storage reliability warning', symptoms: ['Windows reports Warning/Unhealthy', 'Uncorrected errors', 'Device wear near estimated limit'], causes: ['Storage medium degradation', 'Controller, cabling or driver problem', 'Vendor-specific reporting limitations'], checks: ['Back up important data first.', 'Verify with the manufacturer diagnostic tool.', 'Do not run repair or destructive surface tests automatically.'], prevention: ['Maintain tested backups.'], actions: ['openStorageSettings'], sources: ['https://learn.microsoft.com/en-us/windows-hardware/drivers/storage/msft-storagereliabilitycounter'] },
  { id: 'battery-wear', title: 'Reduced reported battery capacity', symptoms: ['Full-charge/design capacity ratio below 80%'], causes: ['Battery aging', 'Firmware calibration or charge limits', 'Temperature or recent charging history'], checks: ['Compare capacity over several charge cycles.', 'Check OEM diagnostics and charging limits.', 'A capacity ratio is not a prediction of imminent failure.'], prevention: ['Follow the device maker charging and temperature guidance.'], actions: ['openPowerSettings'], sources: ['https://learn.microsoft.com/en-us/windows/win32/power/battery-status-str'] },
  { id: 'battery-drain', title: 'Rapid battery depletion', symptoms: ['Measured unplugged charge decline above the local baseline or a conservative heuristic'], causes: ['High CPU activity', 'High-performance power plan', 'Display, radios or external devices', 'Reduced usable battery capacity'], checks: ['Correlate the same unplugged period with CPU and processes.', 'Compare an idle run under similar brightness and workload.', 'Inspect capacity separately from current consumption.'], prevention: ['Use an appropriate power plan.', 'Reduce unneeded background activity.'], actions: ['setBalanced', 'openTaskManager', 'openPowerSettings'], sources: [] },
  { id: 'power-plan', title: 'High-performance plan while unplugged', symptoms: ['Known High performance plan active on battery'], causes: ['User-selected performance preference', 'Application or vendor power policy'], checks: ['Check whether the workload requires this mode.', 'Balanced is reversible but may reduce peak performance.'], prevention: ['Choose a plan matching current work.'], actions: ['setBalanced', 'openPowerSettings'], sources: [] },
  { id: 'unexpected-shutdown', title: 'Unexpected restart or hardware event', symptoms: ['Kernel-Power 41 or WHEA event metadata'], causes: ['Power interruption or forced shutdown', 'Driver or OS crash', 'Hardware instability'], checks: ['Inspect the corresponding Windows event and crash data.', 'Kernel-Power 41 identifies an unclean shutdown, not its root cause.', 'Do not assume battery failure from an event ID alone.'], prevention: ['Save work, keep backups and investigate recurring events.'], actions: ['openEventViewer'], sources: ['https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.diagnostics/get-winevent'] },
  { id: 'app-instability', title: 'Application instability', symptoms: ['Repeated crash events or consecutive nonresponding samples'], causes: ['Busy UI thread', 'Application bug or extension', 'Resource pressure'], checks: ['Distinguish a busy window from a crash.', 'Save work and review application updates.', 'Never terminate a process based only on its name or one sample.'], prevention: ['Keep supported applications current.'], actions: ['openTaskManager', 'openEventViewer'], sources: [] },
  { id: 'baseline-anomaly', title: 'Workload differs from observed baseline', symptoms: ['Robust deviation from a sufficiently populated local CPU baseline'], causes: ['A new legitimate workload', 'Background activity', 'An application behaving differently'], checks: ['Compare current work to the baseline context.', 'Statistical outliers are not proof of malware or failure.'], prevention: ['Collect history under representative workloads.'], actions: ['openTaskManager'], sources: [] },
].map(article => enrichArticle({ ...article, synthetic: true, revision: '2026-10-28', disclaimer: 'Synthetic troubleshooting guidance and heuristic thresholds; not a manufacturer diagnosis.' }));

const finite = value => typeof value === 'number' && Number.isFinite(value);
const median = values => { const sorted = values.filter(finite).sort((first, second) => first - second); return sorted.length ? (sorted[Math.floor((sorted.length - 1) / 2)] + sorted[Math.floor(sorted.length / 2)]) / 2 : null; };
function regression(points) {
  if (points.length < 3) return null;
  const meanX = points.reduce((sum, point) => sum + point[0], 0) / points.length;
  const meanY = points.reduce((sum, point) => sum + point[1], 0) / points.length;
  const variance = points.reduce((sum, point) => sum + (point[0] - meanX) ** 2, 0);
  if (!variance) return null;
  const slope = points.reduce((sum, point) => sum + (point[0] - meanX) * (point[1] - meanY), 0) / variance;
  const total = points.reduce((sum, point) => sum + (point[1] - meanY) ** 2, 0);
  const residual = points.reduce((sum, point) => sum + (point[1] - meanY - slope * (point[0] - meanX)) ** 2, 0);
  return { slope, r2: total > 0 ? Math.max(0, 1 - residual / total) : 0, count: points.length, spanHours: points.at(-1)[0] - points[0][0] };
}
function contiguous(samples, predicate, maxGapMs = 120000) {
  const result = [];
  for (let index = samples.length - 1; index >= 0; index--) {
    const sample = samples[index];
    if (!predicate(sample) || (result.length && Date.parse(result.at(-1).time) - Date.parse(sample.time) > maxGapMs)) break;
    result.push(sample);
  }
  return result.reverse();
}
function trend(samples, getter) {
  const valid = samples.filter(sample => finite(getter(sample)));
  if (valid.length < 3) return null;
  const start = Date.parse(valid[0].time);
  return regression(valid.map(sample => [(Date.parse(sample.time) - start) / 3600000, getter(sample)]));
}
function sensorAvailable(issue, sample) {
  if (issue.kbId === 'battery-wear') return sample.battery?.present && sample.battery.designCapacity > 0 && sample.battery.fullCapacity > 0;
  if (issue.kbId === 'battery-drain') return sample.battery?.present && finite(sample.battery.percent) && typeof sample.battery.acOnline === 'boolean';
  if (issue.kbId === 'power-plan') return sample.battery?.present && typeof sample.battery.acOnline === 'boolean' && !!sample.powerPlan?.id;
  if (['cpu-load', 'baseline-anomaly'].includes(issue.kbId)) return finite(sample.cpuPct);
  if (issue.kbId === 'memory-pressure') return finite(sample.memory?.usedPct);
  if (issue.key.startsWith('space:')) return sample.volumes?.some(volume => issue.key === `space:${volume.drive}` && finite(volume.freePct));
  if (issue.key.startsWith('disk:') || issue.key.startsWith('disk-temp:')) {
    const disk = sample.disks?.find(item => issue.key.endsWith(`:${item.id}`));
    if (!disk) return false;
    if (issue.key.startsWith('disk-temp:')) return finite(disk.temperatureC);
    const evidence = issue.evidence.join(' ');
    return !!disk.health && (!/wear: [0-9]/.test(evidence) || finite(disk.wearPct)) && (!/uncorrected errors: [0-9]/.test(evidence) || finite(disk.uncorrectedErrors));
  }
  if (issue.key.startsWith('hung:')) return String(sample.coverage?.processes).startsWith('ok');
  return String(sample.coverage?.logs).includes('System read') && String(sample.coverage?.logs).includes('Application read');
}
function analyze(sample, history) {
  // Compare readings over time so one spike or a missing sensor is not mistaken for a confirmed fault.
  const all = [...history.filter(item => item.time < sample.time), sample].sort((first, second) => first.time.localeCompare(second.time));
  const recent = contiguous(all, () => true, Math.max(120000, (sample.intervalSeconds || 30) * 2500));
  const issues = [];
  const risks = [];
  const limitations = ['No model here predicts physical failure dates, swelling or fire. Missing sensor data is unknown, not healthy.'];
  const add = (key, kbId, title, severity, evidence, causes, forward) => {
    const article = KB.find(item => item.id === kbId);
    issues.push({ key, kbId, title, severity, evidence, causes: causes.map((cause, index) => typeof cause === 'string' ? { label: cause, confidence: index ? 'possible' : 'supported association', why: 'Correlation or diagnostic rule, not a proven cause.' } : cause), checks: article.checks, nextActions: article.actions, backwardTrace: [evidence.join(' '), ...causes.map(cause => typeof cause === 'string' ? cause : `${cause.label}: ${cause.why}`)], forwardTrace: forward });
  };
  const sustained = (predicate, count = 3) => recent.length >= count && recent.slice(-count).every(predicate) && Date.parse(sample.time) - Date.parse(recent.at(-count).time) >= Math.max(15000, (sample.intervalSeconds || 30) * 1000);
  const topCpu = sample.processes?.filter(item => finite(item.cpuPct)).sort((first, second) => second.cpuPct - first.cpuPct)[0];
  if (sustained(item => finite(item.cpuPct) && item.cpuPct >= 85)) add('cpu-load', 'cpu-load', 'Sustained high CPU usage', sample.cpuPct >= 97 ? 'critical' : 'warning', [`CPU ${sample.cpuPct.toFixed(1)}%, sustained across at least three samples.`, topCpu ? `Largest observed process: ${topCpu.name} (${topCpu.cpuPct.toFixed(1)}% normalized CPU).` : 'Process attribution unavailable.'], ['Application or background workload', 'Updates, indexing or driver activity'], ['Continued load may increase power use, heat and response latency.']);
  if (sustained(item => item.memory?.usedPct >= 90)) add('memory-pressure', 'memory-pressure', 'Sustained memory pressure', sample.memory.usedPct >= 97 ? 'critical' : 'warning', [`RAM ${sample.memory.usedPct.toFixed(1)}% used; ${(sample.memory.availableBytes / 1073741824).toFixed(2)} GiB available.`], ['Application working sets or many browser tabs', 'Possible growing application allocation'], ['Further growth may cause paging or application allocation failures.']);
  for (const volume of sample.volumes || []) {
    if (finite(volume.freePct) && volume.freePct < 10) add(`space:${volume.drive}`, 'disk-space', `Low free space on ${volume.drive}`, volume.freePct < 5 ? 'critical' : 'warning', [`${volume.freePct.toFixed(1)}% free (${(volume.freeBytes / 1073741824).toFixed(1)} GiB).`], ['Files, application caches or updates consuming capacity'], ['Updates, temporary files and applications may fail if the volume fills.']);
    const hourly = [];
    for (const item of all) if (!hourly.length || Date.parse(item.time) - Date.parse(hourly.at(-1).time) >= 3600000) hourly.push(item);
    const fitted = trend(hourly, item => item.volumes?.find(candidate => candidate.drive === volume.drive)?.freePct);
    if (fitted?.count >= 12 && fitted.spanHours >= 24 && fitted.slope < -0.01 && fitted.r2 >= 0.7 && volume.freePct > 10 && volume.freePct < 25) {
      const hours = (volume.freePct - 10) / -fitted.slope;
      if (hours < 168) risks.push({ key: `space-trend:${volume.drive}`, title: `${volume.drive} could reach 10% free in about ${Math.round(hours)} hours`, confidence: 'trend extrapolation', evidence: `${fitted.count} hourly observations, ${fitted.spanHours.toFixed(1)} hours, R² ${fitted.r2.toFixed(2)}. Assumes unchanged workload; not a failure forecast.` });
    }
  }
  for (const disk of sample.disks || []) {
    if (['Warning', 'Unhealthy'].includes(disk.health) || (finite(disk.wearPct) && disk.wearPct >= 90) || disk.uncorrectedErrors > 0) add(`disk:${disk.id}`, 'storage-health', `Storage reliability signal: disk ${disk.id}`, disk.health === 'Unhealthy' || disk.wearPct >= 100 || disk.uncorrectedErrors > 0 ? 'critical' : 'warning', [`Windows health: ${disk.health || 'unavailable'}; wear: ${disk.wearPct ?? 'unknown'}%; uncorrected errors: ${disk.uncorrectedErrors ?? 'unknown'}.`], ['Device-reported reliability condition', 'Controller or reporting issue still possible'], ['Back up important data now; persistent errors can precede data loss. No failure date can be inferred.']);
    if (finite(disk.temperatureC) && disk.temperatureC >= 70) add(`disk-temp:${disk.id}`, 'storage-health', `Elevated reported storage temperature: disk ${disk.id}`, 'warning', [`${disk.temperatureC}°C; 70°C is a generic triage threshold, not this model’s rated limit.`], ['Sustained I/O or restricted cooling', 'Sensor or firmware reporting issue'], ['Check the manufacturer’s operating limits and cooling before concluding damage.']);
  }
  const battery = sample.battery;
  if (battery?.present) {
    const health = finite(battery.designCapacity) && finite(battery.fullCapacity) && battery.designCapacity > 0 && battery.fullCapacity > 0 ? battery.fullCapacity / battery.designCapacity * 100 : null;
    if (finite(health) && health < 80) add('battery-wear', 'battery-wear', 'Reduced reported battery capacity', health < 60 ? 'critical' : 'warning', [`Full/design capacity ratio ${health.toFixed(1)}%. Source: ${battery.capacitySource || 'firmware'}.`], ['Battery aging is plausible', 'Calibration or charge-limit behavior can affect reported capacity'], ['Usable unplugged runtime may be shorter. This does not establish imminent battery failure.']);
    if (!finite(health)) limitations.push('Battery design/full capacity unavailable: degradation cannot be estimated.');
    if (battery.acOnline === false && sample.powerPlan?.id?.toLowerCase() === '8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c') add('power-plan', 'power-plan', 'High-performance power plan on battery', 'info', ['Known High performance plan is active while AC power is offline.'], ['Power policy may favor performance over runtime'], ['A lighter power policy may reduce consumption; the improvement must be measured.']);
    const unplugged = contiguous(recent, item => item.battery?.acOnline === false && item.battery?.charging === false && finite(item.battery?.percent));
    const fitted = trend(unplugged.slice(-120), item => item.battery.percent);
    const baselineRates = [];
    for (let index = 10; index < all.length - 20; index += 10) {
      const first = all[index - 10], last = all[index];
      const duration = (Date.parse(last.time) - Date.parse(first.time)) / 3600000;
      if (duration >= 0.05 && duration <= 0.2 && all.slice(index - 10, index + 1).every(item => item.battery?.acOnline === false && item.battery?.charging === false && finite(item.battery.percent))) {
        const rate = (first.battery.percent - last.battery.percent) / duration;
        if (rate > 0 && rate < 100) baselineRates.push(rate);
      }
    }
    const rateMedian = median(baselineRates), deviation = rateMedian === null ? null : median(baselineRates.map(rate => Math.abs(rate - rateMedian)));
    const threshold = baselineRates.length >= 20 ? Math.max(15, rateMedian + 3 * 1.4826 * Math.max(1, deviation)) : 15;
    if (fitted?.count >= 6 && fitted.spanHours >= 8 / 60 && fitted.r2 >= 0.6 && fitted.slope < 0) {
      const rate = -fitted.slope;
      if (rate >= threshold) add('battery-drain', 'battery-drain', 'Rapid unplugged charge decline', 'warning', [`${rate.toFixed(1)} percentage points/hour over ${(fitted.spanHours * 60).toFixed(0)} minutes (R² ${fitted.r2.toFixed(2)}).`, baselineRates.length >= 20 ? `Adaptive threshold ${threshold.toFixed(1)} points/hour from ${baselineRates.length} historical windows.` : 'History insufficient for a personal baseline; using a 15 points/hour heuristic.'], [sample.cpuPct >= 60 ? 'Concurrent CPU activity supports a workload contribution' : 'CPU is not currently high; check display, radios and workload history', sample.powerPlan?.name?.includes('High') ? 'Performance-focused power policy may contribute' : 'Display brightness, radios or external devices are unmeasured', finite(health) && health < 80 ? 'Reduced capacity may shorten runtime' : 'Hardware degradation is not established'], ['If this workload continues, charge may reach low levels sooner than usual.']);
      const hours = (battery.percent - 10) / rate;
      if (hours > 0 && hours < 8) risks.push({ key: 'battery-runtime', title: `Could reach 10% charge in about ${Math.round(hours * 60)} minutes`, confidence: 'short-term extrapolation', evidence: `Measured ${rate.toFixed(1)} percentage points/hour across ${(fitted.spanHours * 60).toFixed(0)} minutes. Assumes unchanged load; not a battery-failure prediction.` });
    } else limitations.push('Need at least eight uninterrupted unplugged minutes and a consistent charge trend for runtime/drain estimates.');
    if (finite(battery.percent) && battery.percent <= 10 && battery.acOnline === false) add('battery-low', 'battery-drain', 'Low battery charge', battery.percent <= 5 ? 'critical' : 'warning', [`Charge ${battery.percent}%; AC offline.`], ['Battery is being depleted'], ['Save work and connect a suitable charger to reduce unexpected shutdown risk.']);
  } else limitations.push('No battery was detected, or the battery provider is unavailable.');
  const memoryWindow = recent.filter(item => Date.parse(item.time) >= Date.parse(sample.time) - 1800000);
  const memoryTrend = trend(memoryWindow, item => item.memory?.usedPct);
  if (memoryTrend?.spanHours >= 1 / 6 && memoryTrend.count >= 10 && memoryTrend.r2 >= 0.8 && memoryTrend.slope > 1 && sample.memory?.usedPct > 75 && sample.memory.usedPct < 95) {
    const minutes = (95 - sample.memory.usedPct) / memoryTrend.slope * 60;
    if (minutes < 120) risks.push({ key: 'memory-growth', title: `RAM use could reach 95% in about ${Math.round(minutes)} minutes`, confidence: 'trend extrapolation', evidence: `Linear trend R² ${memoryTrend.r2.toFixed(2)}, ${(memoryTrend.spanHours * 60).toFixed(0)} minutes. Changing workloads invalidate this estimate; not proof of a leak.` });
  }
  const cpuHistory = history.filter(item => item.battery?.acOnline === battery?.acOnline).map(item => item.cpuPct).filter(finite);
  const cpuMedian = median(cpuHistory), cpuMad = cpuMedian === null ? null : median(cpuHistory.map(value => Math.abs(value - cpuMedian)));
  const cpuZ = cpuMedian === null || !finite(sample.cpuPct) ? null : (sample.cpuPct - cpuMedian) / (1.4826 * Math.max(5, cpuMad));
  if (cpuHistory.length >= 60 && cpuZ >= 4 && sample.cpuPct > 40 && !issues.some(issue => issue.key === 'cpu-load')) add('baseline-anomaly', 'baseline-anomaly', 'CPU above its observed local baseline', 'info', [`CPU ${sample.cpuPct.toFixed(1)}%; baseline median ${cpuMedian.toFixed(1)}%; robust deviation ${cpuZ.toFixed(1)}.`], ['Changed workload, not necessarily a fault'], ['Observe persistence before deciding that intervention is needed.']);
  const logs = (sample.logs || []).filter(event => Date.parse(event.time) >= Date.parse(sample.time) - 3600000);
  const shutdowns = logs.filter(event => event.id === 41 && /Kernel-Power/i.test(event.provider));
  const hardware = logs.filter(event => [18, 19].includes(event.id) && /WHEA/i.test(event.provider));
  if (shutdowns.length || hardware.length) add('system-events', 'unexpected-shutdown', 'Recent restart or hardware error signal', hardware.some(event => event.id === 18) ? 'critical' : 'warning', [...shutdowns, ...hardware].slice(0, 5).map(event => `${event.provider} / ${event.id} at ${event.time}.`), ['Power interruption, crash or hardware/driver instability; event metadata alone cannot distinguish them'], ['Recurring events warrant reviewing full logs locally and vendor diagnostics.']);
  const storageEvents = logs.filter(event => [7, 51, 55, 153].includes(event.id) && /disk|ntfs|storport/i.test(event.provider));
  if (storageEvents.length) add('storage-events', 'storage-health', 'Recent storage error event', storageEvents.some(event => event.id === 7) ? 'critical' : 'warning', storageEvents.slice(0, 4).map(event => `${event.provider} / ${event.id} at ${event.time}.`), ['Storage device, controller, filesystem or driver issue'], ['Back up before troubleshooting a recurring storage error.']);
  const crashes = logs.filter(event => event.id === 1000 && /Application Error/i.test(event.provider) && Date.parse(event.time) >= Date.parse(sample.time) - 900000);
  if (crashes.length >= 3) add('app-crashes', 'app-instability', 'Repeated recent application crash events', 'warning', [`${crashes.length} Application Error 1000 records in the available 15-minute window; events may refer to different apps.`], ['One or several unstable applications', 'Resource pressure or application extension issue'], ['Inspect event details locally to identify the affected application before changing software.']);
  for (const process of sample.processes || []) if (process.responding === false && sustained(item => item.processes?.some(candidate => candidate.pid === process.pid && candidate.started === process.started && candidate.responding === false))) add(`hung:${process.pid}:${process.started}`, 'app-instability', `${process.name} is repeatedly not responding`, 'warning', ['At least three observations of the same process identity. A busy window can look unresponsive.'], ['Busy or blocked application UI thread'], ['Save work where possible and inspect the app before closing it.']);
  if (all.length < 60) limitations.push('Local statistical baseline is warming up; no trained failure-prediction model is installed.');
  if (!all.length || Date.parse(sample.time) - Date.parse(all[0].time) < 86400000) limitations.push('Need at least 24 hours of hourly observations for disk-space trend projection.');
  return { issues, risks, limitations, baseline: { samples: all.length, cpuMedian, cpuMad, cpuRobustZ: cpuZ, method: 'Median/MAD anomaly detection and gated least-squares trend extrapolation; not a trained hardware-failure classifier.' } };
}

const stores = new Map();
function createStore(root = process.cwd()) {
  // Keep measured history separate from conversations, with local storage and limited retention.
  const directory = path.join(root, '.ultron');
  fs.mkdirSync(directory, { recursive: true });
  if (stores.has(directory)) return stores.get(directory);
  const db = new DatabaseSync(path.join(directory, 'health.sqlite'));
  db.exec('PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000; CREATE TABLE IF NOT EXISTS samples(id TEXT PRIMARY KEY,time TEXT NOT NULL,data TEXT NOT NULL); CREATE INDEX IF NOT EXISTS sample_time ON samples(time); CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL); CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,time TEXT NOT NULL,kind TEXT NOT NULL,detail TEXT NOT NULL); CREATE TABLE IF NOT EXISTS issues(key TEXT PRIMARY KEY,data TEXT NOT NULL); CREATE TABLE IF NOT EXISTS analyses(id TEXT PRIMARY KEY,time TEXT NOT NULL,text TEXT NOT NULL);');
  const setting = (key, fallback) => { const row = db.prepare('SELECT value FROM settings WHERE key=?').get(key); return row ? JSON.parse(row.value) : fallback; };
  const set = (key, value) => db.prepare('INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value').run(key, JSON.stringify(value));
  const event = (kind, detail) => db.prepare('INSERT INTO events VALUES(?,?,?,?)').run(randomUUID(), new Date().toISOString(), kind, String(detail).slice(0, 2000));
  const history = (hours = 24, limit = 2000) => db.prepare('SELECT data FROM samples WHERE time>=? ORDER BY time DESC LIMIT ?').all(new Date(Date.now() - hours * 3600000).toISOString(), limit).map(row => JSON.parse(row.data)).reverse();
  const getSample = id => { const row = db.prepare('SELECT data FROM samples WHERE id=?').get(id); return row ? JSON.parse(row.data) : null; };
  const getIssues = () => db.prepare('SELECT data FROM issues').all().map(row => JSON.parse(row.data)).sort((first, second) => second.lastSeen.localeCompare(first.lastSeen));
  const config = () => ({ enabled: false, intervalSeconds: 30, retentionDays: 7, ...setting('config', {}) });
  const latest = () => { const row = db.prepare('SELECT data FROM samples ORDER BY time DESC LIMIT 1').get(); return row ? JSON.parse(row.data) : null; };
  function saveSample(sample) {
    db.exec('BEGIN IMMEDIATE');
    try {
      db.prepare('INSERT OR REPLACE INTO samples VALUES(?,?,?)').run(sample.id, sample.time, JSON.stringify(sample));
      const previous = getIssues();
      for (const issue of sample.analysis.issues) {
        const before = previous.find(item => item.key === issue.key);
        const next = { ...issue, firstSeen: before?.firstSeen || sample.time, lastSeen: sample.time, status: 'active', missing: 0 };
        db.prepare('INSERT INTO issues VALUES(?,?) ON CONFLICT(key) DO UPDATE SET data=excluded.data').run(issue.key, JSON.stringify(next));
        if (!before || before.status === 'resolved' || before.severity !== issue.severity) event('detection', `${issue.severity.toUpperCase()}: ${issue.title}. ${issue.evidence.join(' ')}`);
      }
      for (const before of previous.filter(item => item.status === 'active' && !sample.analysis.issues.some(issue => issue.key === item.key))) {
        const available = sensorAvailable(before, sample);
        if (!available) { before.unverified = true; before.missing = 0; before.lastRecoveryCheck = null; db.prepare('UPDATE issues SET data=? WHERE key=?').run(JSON.stringify(before), before.key); continue; }
        before.unverified = false;
        const recoveryGap = Date.parse(sample.time) - Date.parse(before.lastRecoveryCheck || '');
        if (!Number.isFinite(recoveryGap) || recoveryGap <= 0 || recoveryGap > Math.max(120000, sample.intervalSeconds * 2500)) before.missing = 0;
        before.missing = (before.missing || 0) + 1;
        before.lastRecoveryCheck = sample.time;
        if (before.missing >= 3) { before.status = 'resolved'; before.resolvedAt = sample.time; event('resolution', `Signal no longer observed across three polls: ${before.title}. This is not proof of permanent repair.`); }
        db.prepare('UPDATE issues SET data=? WHERE key=?').run(JSON.stringify(before), before.key);
      }
      const cutoff = new Date(Date.parse(sample.time) - config().retentionDays * 86400000).toISOString();
      const previousRisks = setting('riskKeys', []);
      for (const risk of sample.analysis.risks) if (!previousRisks.includes(risk.key)) event('projection', `${risk.title}. ${risk.evidence}`);
      set('riskKeys', sample.analysis.risks.map(risk => risk.key));
      db.prepare('DELETE FROM samples WHERE time<?').run(cutoff);
      db.prepare('DELETE FROM samples WHERE id NOT IN (SELECT id FROM samples ORDER BY time DESC LIMIT 20160)').run();
      db.prepare('DELETE FROM events WHERE time<? OR id NOT IN (SELECT id FROM events ORDER BY time DESC LIMIT 2000)').run(cutoff);
      db.prepare('DELETE FROM analyses WHERE time<? OR id NOT IN (SELECT id FROM analyses ORDER BY time DESC LIMIT 30)').run(cutoff);
      db.prepare('DELETE FROM issues WHERE key NOT IN (SELECT key FROM issues ORDER BY json_extract(data,\'$.lastSeen\') DESC LIMIT 200)').run();
      db.exec('COMMIT');
    } catch (error) { db.exec('ROLLBACK'); throw error; }
  }
  const modelHistory = () => db.prepare("SELECT data FROM samples WHERE time>=? AND (time>=? OR time IN (SELECT MAX(time) FROM samples GROUP BY substr(time,1,13))) ORDER BY time").all(new Date(Date.now() - 7 * 86400000).toISOString(), new Date(Date.now() - 7200000).toISOString()).map(row => JSON.parse(row.data));
  const chartHistory = (hours = 6) => db.prepare("SELECT data FROM samples WHERE time IN (SELECT MAX(time) FROM samples WHERE time>=? GROUP BY CAST(strftime('%s',time) AS INTEGER) / ?) ORDER BY time").all(new Date(Date.now() - hours * 3600000).toISOString(), Math.max(1, Math.ceil(hours * 3600 / 360))).map(row => JSON.parse(row.data));
  const store = { db, setting, set, event, history, modelHistory, chartHistory, getSample, getIssues, config, latest, saveSample,
    events: () => db.prepare('SELECT * FROM events ORDER BY time DESC LIMIT 100').all(),
    analyses: () => db.prepare('SELECT * FROM analyses ORDER BY time DESC LIMIT 5').all(),
    saveAnalysis(text) { db.prepare('INSERT INTO analyses VALUES(?,?,?)').run(randomUUID(), new Date().toISOString(), String(text).slice(0, 20000)); },
    clear() {
      const worker = setting('worker', {});
      if (config().enabled || (worker.status !== 'stopped' && Date.now() - (worker.heartbeat || 0) < 45000)) throw new Error('Stop the collector and wait for it to exit before clearing history.');
      db.exec('BEGIN IMMEDIATE');
      try {
        db.exec("DELETE FROM samples; DELETE FROM events; DELETE FROM issues; DELETE FROM analyses; DELETE FROM settings WHERE key NOT IN ('config','worker');");
        require('./shadow/store.cjs').clearShadow(db);
        db.exec('COMMIT');
      } catch (error) { db.exec('ROLLBACK'); throw error; }
      db.exec('PRAGMA wal_checkpoint(TRUNCATE); VACUUM;');
    }
  };
  stores.set(directory, store);
  return store;
}
module.exports = { KB, analyze, createStore, regression, median, contiguous };
