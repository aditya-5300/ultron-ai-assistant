const fs = require('node:fs');
const path = require('node:path');
const { randomUUID } = require('node:crypto');
const { createStore, analyze } = require('../lib/health/core.cjs');
const { createCollector } = require('../lib/health/collector.cjs');
const root = path.resolve(__dirname, '..');
const store = createStore(root);
const lockPath = path.join(root, '.ultron', 'health-worker.lock');
const nonce = randomUUID();
let stopping = false;
function alive(pid) { try { process.kill(pid, 0); return true; } catch (error) { return error.code === 'EPERM'; } }
function acquire() {
  // A lock prevents two collectors from recording duplicate readings for the same project.
  try { fs.writeFileSync(lockPath, JSON.stringify({ pid: process.pid, nonce }), { flag: 'wx' }); }
  catch (error) {
    if (error.code !== 'EEXIST') throw error;
    let previous;
    try { previous = JSON.parse(fs.readFileSync(lockPath, 'utf8')); } catch { throw new Error('Worker lock is unreadable. Do not start another collector until the lock is reviewed.'); }
    if (alive(previous.pid)) return false;
    fs.unlinkSync(lockPath);
    fs.writeFileSync(lockPath, JSON.stringify({ pid: process.pid, nonce }), { flag: 'wx' });
  }
  return true;
}
function heartbeat(status, error) { store.set('worker', { pid: process.pid, nonce, heartbeat: Date.now(), status, error: error || null }); }
async function main() {
  // Keep collecting independently of the browser until monitoring is stopped or the session ends.
  if (!acquire()) { console.log('ULTRON monitoring collector already running.'); return; }
  if (!process.argv.includes('--background')) store.set('config', { ...store.config(), enabled: true });
  const collect = createCollector(root);
  store.event('lifecycle', 'Monitoring collector started. Local counters only; no desktop capture or automatic system changes.');
  const pulse = setInterval(() => heartbeat(stopping ? 'stopping' : 'running'), 10000);
  pulse.unref();
  heartbeat('starting');
  try {
    while (!stopping && store.config().enabled) {
      const started = Date.now();
      try {
        const config = store.config();
        const sample = await collect(config.intervalSeconds);
        if (stopping || !store.config().enabled) break;
        sample.analysis = analyze(sample, store.modelHistory());
        store.saveSample(sample);
        store.set('lastSample', sample.time);
        heartbeat('running');
      } catch (error) { heartbeat('error', 'Telemetry collection failed; old samples are not current.'); store.event('collector-error', String(error.message || error)); }
      while (!stopping && store.config().enabled && store.setting('resample', 0) <= started && Date.now() - started < store.config().intervalSeconds * 1000) await new Promise(resolve => setTimeout(resolve, 1000));
    }
  } finally {
    clearInterval(pulse); heartbeat('stopped'); store.event('lifecycle', 'Monitoring collector stopped.');
    try { if (JSON.parse(fs.readFileSync(lockPath, 'utf8')).nonce === nonce) fs.unlinkSync(lockPath); } catch {}
  }
}
process.on('SIGTERM', () => { stopping = true; });
process.on('SIGINT', () => { stopping = true; });
main().catch(error => { console.error(error.message); store.event('collector-error', error.message); heartbeat('error', error.message); process.exitCode = 1; });
