const { parentPort, workerData } = require('node:worker_threads');
const { DatabaseSync } = require('node:sqlite');
const { performance } = require('node:perf_hooks');
const { setTimeout: wait } = require('node:timers/promises');
const { checkpoint, consume, recordRun } = require('../lib/health/shadow/store.cjs');
const { exportReplay } = require('../lib/health/shadow/export.cjs');
const { writerAdmission } = require('../lib/health/shadow/admission.cjs');
const { traceTransactions } = require('./phase8d-timing.cjs');
const config = require('../docs/phase8b-reference/phase8b_config.json');

async function main() {
  const { file, start, count, mode, activity } = workerData;
  if (mode === 'disabled') return [];
  const db = new DatabaseSync(file);
  const sleeper = new Int32Array(new SharedArrayBuffer(4));
  const admission = mode === 'phase8d' ? writerAdmission(activity) : null;
  let injection = 0, slept = 0;
  db.function('fixture_hold', () => {
    const began = performance.now();
    const result = Atomics.wait(sleeper, 0, 0, injection);
    slept = performance.now() - began;
    return result;
  });
  db.exec(`PRAGMA busy_timeout=${config.shadow_busy_timeout_ms};
    CREATE TEMP TRIGGER fixture_hold_write BEFORE INSERT ON shadow_runs BEGIN SELECT fixture_hold(); END`);
  const timing = traceTransactions(db);
  const replay = exportReplay(db, 'seed-399', checkpoint(db).installation, checkpoint(db));
  const samples = [];
  try {
    for (let index = 0; index < count; index++) {
      const due = start + index * config.shadow_interval_ms;
      if (Date.now() < due) await wait(due - Date.now());
      const began = performance.now(), lateMs = Math.max(0, Date.now() - due);
      injection = index % 25 === 0 ? 20 : index % 5 === 0 ? 5 : 0;
      slept = 0;
      let deferredMs = 0, outcome = 'ok';
      const stamp = timing.length;
      try {
        if (admission) deferredMs = await admission.wait();
        consume(db, replay, [], checkpoint(db), () => recordRun(db, 'SHADOW', 'OK', 0, {}));
      } catch (error) {
        if (error.code === 'WRITER_DEFERRED') { outcome = 'deferred'; deferredMs = performance.now() - began; }
        else if ([5, 6].includes(error.errcode & 255)) outcome = 'busy';
        else throw error;
      }
      const durationMs = performance.now() - began;
      const transaction = timing[stamp] || { waitMs: null, heldMs: null };
      samples.push({ outcome, deferredMs, durationMs, lateMs, injectedMs: injection, sleptMs: slept, ...transaction,
        outsideMs: Math.max(0, durationMs - (transaction.waitMs ?? 0) - (transaction.heldMs ?? 0)) });
    }
    return samples;
  } finally { db.close(); }
}

main().then(result => parentPort.postMessage(result)).catch(error => { throw error; });
