const { parentPort, workerData } = require('node:worker_threads');
const { DatabaseSync } = require('node:sqlite');
const { createJob } = require('./job.cjs');
const { migrate, status } = require('./store.cjs');

const database = new DatabaseSync(workerData.path);
database.exec('PRAGMA busy_timeout=50');
let job;
try {
  job = createJob(database, workerData.config);
} catch {
  try {
    migrate(database);
    status(database, 'DEGRADED', 'INVALID_CONFIG');
  } catch {}
  job = async () => ({ ok: false, transient: false });
}
parentPort.on('message', async sampleId => {
  try { parentPort.postMessage(await job(sampleId)); }
  catch { parentPort.postMessage({ ok: false, transient: true }); }
});
