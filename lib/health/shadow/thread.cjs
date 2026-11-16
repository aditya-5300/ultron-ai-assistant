const { parentPort, workerData } = require('node:worker_threads');
const { DatabaseSync } = require('node:sqlite');
const { createJob } = require('./job.cjs');
const { migrateObservability, recordRun, status } = require('./store.cjs');
const { writerAdmission } = require('./admission.cjs');

const database = new DatabaseSync(workerData.path);
database.exec('PRAGMA busy_timeout=50');
let job;
const admission = workerData.activity ? writerAdmission(workerData.activity) : null;
try {
  job = createJob(database, workerData.config, admission);
} catch {
  job = async () => {
    try {
      await admission?.wait();
      migrateObservability(database);
      status(database, 'DEGRADED', 'INVALID_CONFIG');
      recordRun(database, 'DEGRADED', 'INVALID_CONFIG', 0, {});
    } catch {}
    return { ok: false, transient: false };
  };
}
parentPort.on('message', async message => {
  try { parentPort.postMessage(await (typeof message === 'string' ? job(message) : job(message.id, message.scheduler))); }
  catch { parentPort.postMessage({ ok: false, transient: true }); }
});
