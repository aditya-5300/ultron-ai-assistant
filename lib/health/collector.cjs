const os = require('node:os');
const path = require('node:path');
const { execFile } = require('node:child_process');
const { promisify } = require('node:util');
const { randomUUID } = require('node:crypto');
const exec = promisify(execFile);
const windowsPowerShell = () => path.join(process.env.SystemRoot || 'C:\\Windows', 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe');
function cpuTotals() {
  return os.cpus().reduce((result, processor) => { result.idle += processor.times.idle; result.total += Object.values(processor.times).reduce((sum, value) => sum + value, 0); result.count += 1; return result; }, { idle: 0, total: 0, count: 0 });
}
function createCollector(root = process.cwd()) {
  // Compare two readings to measure current activity instead of treating lifetime CPU time as usage.
  let previousCpu = null;
  let previousProcesses = new Map();
  let previousTime = 0;
  let deepCache = null;
  let deepAttempt = 0;
  return async function collect(intervalSeconds = 30) {
    const startedAt = Date.now();
    let raw = { coverage: {}, processes: [], battery: null, powerPlan: null };
    const deep = !deepAttempt || startedAt - deepAttempt >= 300000;
    if (process.platform === 'win32') {
      try {
        if (deep) deepAttempt = startedAt;
        const { stdout } = await exec(windowsPowerShell(), ['-NoProfile', '-NonInteractive', '-File', path.join(root, 'scripts', 'collect-health.ps1'), ...(deep ? ['-Deep'] : [])], { cwd: root, windowsHide: true, timeout: 45000, maxBuffer: 2097152, encoding: 'utf8' });
        raw = JSON.parse(stdout.trim().replace(/^\uFEFF/, ''));
        if (deep) deepCache = { volumes: raw.volumes || [], disks: raw.disks || [], logs: raw.logs || [], deepTime: raw.deepTime, coverage: { ...raw.coverage }, batteryCapacity: raw.battery ? { fullCapacity: raw.battery.fullCapacity, designCapacity: raw.battery.designCapacity, cycles: raw.battery.cycles, capacitySource: raw.battery.capacitySource } : null };
      } catch { raw.coverage = { windows: 'unavailable: Windows telemetry query failed or timed out; no elevation or policy bypass attempted' }; }
    } else raw.coverage.windows = 'unavailable: Windows battery, storage, process and log collectors are not implemented on this OS';
    const time = Date.now();
    const totals = cpuTotals();
    const delta = previousCpu && previousCpu.count === totals.count ? totals.total - previousCpu.total : 0;
    const elapsed = (time - previousTime) / 1000;
    const cpuPct = delta > 0 && elapsed > 0 && elapsed <= Math.max(120, intervalSeconds * 3) ? Math.max(0, Math.min(100, (1 - (totals.idle - previousCpu.idle) / delta) * 100)) : null;
    previousCpu = totals;
    const currentProcesses = new Map();
    const processes = (raw.processes || []).filter(process => process.pid > 0).map(process => {
      const key = `${process.pid}:${process.started}`;
      const previous = previousProcesses.get(key);
      const cpuDelta = typeof process.cpuSeconds === 'number' && typeof previous === 'number' ? process.cpuSeconds - previous : null;
      const percent = process.started && elapsed > 0 && elapsed <= Math.max(120, intervalSeconds * 3) && cpuDelta !== null && cpuDelta >= 0 ? Math.max(0, Math.min(100, cpuDelta / elapsed / Math.max(1, os.cpus().length) * 100)) : null;
      currentProcesses.set(key, process.cpuSeconds);
      return { pid: process.pid, name: String(process.name).slice(0, 80), started: process.started, cpuPct: percent, memoryBytes: process.workingSetBytes, responding: process.responding };
    });
    previousProcesses = currentProcesses; previousTime = time;
    const top = [...processes].sort((first, second) => (second.cpuPct || 0) - (first.cpuPct || 0)).slice(0, 8);
    const memory = [...processes].sort((first, second) => second.memoryBytes - first.memoryBytes).slice(0, 8);
    const hung = processes.filter(process => process.responding === false).slice(0, 8);
    const selected = [...new Map([...top, ...memory, ...hung].map(process => [process.pid, process])).values()];
    const cacheFresh = deepCache && time - Date.parse(deepCache.deepTime) < 660000;
    const totalBytes = os.totalmem(), availableBytes = os.freemem();
    return { id: randomUUID(), time: new Date(time).toISOString(), platform: process.platform, intervalSeconds, uptimeSeconds: os.uptime(), cpuPct, memory: { totalBytes, availableBytes, usedPct: totalBytes > 0 ? (1 - availableBytes / totalBytes) * 100 : null }, processes: selected,
      battery: raw.battery ? { ...raw.battery, ...(cacheFresh && raw.battery.count === 1 && deepCache.batteryCapacity ? deepCache.batteryCapacity : {}) } : null,
      powerPlan: raw.powerPlan || null, volumes: cacheFresh ? deepCache.volumes : [], disks: cacheFresh ? deepCache.disks : [], logs: cacheFresh ? deepCache.logs : [], deepTime: cacheFresh ? deepCache.deepTime : null,
      coverage: { ...(cacheFresh ? deepCache.coverage : {}), ...raw.coverage, cpu: cpuPct === null ? 'warming up: a second observation is needed' : 'ok: OS CPU-time deltas across the polling interval', memory: 'ok: physical memory, not commit/pagefile or per-app private bytes', desktop: 'not collected: no screenshots, titles, keystrokes, browser history or command lines', ...(cacheFresh ? {} : { deepSensors: 'unavailable or stale: storage/log/capacity data excluded' }) }, durationMs: time - startedAt };
  };
}
module.exports = { createCollector, cpuTotals, windowsPowerShell };
