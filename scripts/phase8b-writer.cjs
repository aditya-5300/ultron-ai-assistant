const { parentPort, workerData } = require('node:worker_threads');
const { DatabaseSync } = require('node:sqlite');
const { performance } = require('node:perf_hooks');
const { setTimeout: wait } = require('node:timers/promises');
const { checkpoint, consume, recordRun } = require('../lib/health/shadow/store.cjs');
const { exportReplay } = require('../lib/health/shadow/export.cjs');
const config = require('../docs/phase8b-reference/phase8b_config.json');

async function main() {
  const { file, start, count, enabled } = workerData;
  if (!enabled) return { ok: 0, busy: 0, durations: [] };
  const db = new DatabaseSync(file);
  const sleeper = new Int32Array(new SharedArrayBuffer(4));
  let hold = 0;
  db.function('fixture_hold', () => Atomics.wait(sleeper, 0, 0, hold));
  db.exec(`PRAGMA busy_timeout=${config.shadow_busy_timeout_ms};
    CREATE TEMP TRIGGER fixture_hold_write BEFORE INSERT ON shadow_runs BEGIN SELECT fixture_hold(); END`);
  const replay = exportReplay(db, 'seed-399', checkpoint(db).installation, checkpoint(db));
  const durations = [];
  let busy = 0, ok = 0;
  try {
    for (let index = 0; index < count; index++) {
      const due = start + index * config.shadow_interval_ms;
      if (Date.now() < due) await wait(due - Date.now());
      const began = performance.now();
      hold = index % 25 === 0 ? 20 : index % 5 === 0 ? 5 : 0;
      try {
        consume(db, replay, [], checkpoint(db), () => recordRun(db, 'SHADOW', 'OK', 0, {}));
        ok++;
      } catch (error) {
        if (![5, 6].includes(error.errcode & 255)) throw error;
        busy++;
      }
      durations.push(performance.now() - began);
    }
    return { ok, busy, durations };
  } finally { db.close(); }
}
main().then(result => parentPort.postMessage(result)).catch(error => { throw error; });
