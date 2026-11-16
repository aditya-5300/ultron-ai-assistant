const assert = require('node:assert/strict');
const { test } = require('node:test');
const { performance } = require('node:perf_hooks');
const { Worker } = require('node:worker_threads');
const { once } = require('node:events');
const { fixture, service, seed, sample } = require('./phase8b-fixture.cjs');
const { analyze } = require('../lib/health/core.cjs');
const { createJob } = require('../lib/health/shadow/job.cjs');
const { migrateObservability, checkpoint } = require('../lib/health/shadow/store.cjs');
const { writerAdmission, markDeterministic } = require('../lib/health/shadow/admission.cjs');
const { traceTransactions } = require('./phase8d-timing.cjs');

test('verified replay defers behind a stalled activity hint and safely reconciles on retry', {
  skip: !process.env.ULTRON_PHASE8B_PYTHON, timeout: 60000
}, async context => {
  const host = fixture(context);
  const { db } = host.store;
  seed(db, 400, Date.now() - 400 * 15000, 15, true);
  migrateObservability(db);
  const python = await service(host);
  await python.start();
  const signal = new SharedArrayBuffer(4);
  const admission = writerAdmission(signal);
  const run = createJob(db, python, admission);
  const saved = checkpoint(db);
  markDeterministic(signal, true);
  const started = performance.now();
  const inFlight = run('seed-399');
  const item = sample('critical-during-deferral', Date.now(), 15, true);
  item.analysis = analyze(item, []);
  host.store.saveSample(item);
  assert.equal(host.store.getIssues().find(issue => issue.key === 'space:C:').severity, 'critical');
  const result = await inFlight;
  const refusedMs = performance.now() - started;
  assert.equal(result.ok, false);
  assert.equal(result.code, 'WRITER_DEFERRED');
  assert.equal(result.recorded, false);
  assert.equal(result.transient, true);
  assert.deepEqual(checkpoint(db), saved);
  assert.equal(db.prepare('SELECT count(*) AS n FROM shadow_status').get().n, 0);
  markDeterministic(signal, false);
  const retry = await run(item.id);
  assert.ok(retry.ok, JSON.stringify(retry));
  assert.ok(retry.rawTransitions > 0);
  const repeated = await run(item.id);
  assert.equal(repeated.openings, 0);
  assert.equal(repeated.hypotheticalInterruptions, 0);
  assert.equal(repeated.repeated, retry.rawTransitions);
  assert.equal(host.store.latest().id, item.id);
  assert.equal(db.prepare('PRAGMA quick_check').get().quick_check, 'ok');
  console.log('PHASE8D ' + JSON.stringify({ kind: 'reconciliation', value: { refusedMs, result, retry, repeated,
    deterministicSamplePersisted: true, checkpointUnchangedOnRefusal: true, criticalFindingPersisted: true } }));
});

test('an already admitted writer can still overlap deterministic persistence without data loss', async context => {
  const host = fixture(context);
  const signal = new SharedArrayBuffer(4);
  const writerPath = require.resolve('../lib/health/shadow/admission.cjs');
  const worker = new Worker(`
    const { parentPort, workerData } = require('node:worker_threads');
    const { DatabaseSync } = require('node:sqlite');
    const { writerAdmission } = require(workerData.writerPath);
    const idle = new Int32Array(workerData.signal);
    const db = new DatabaseSync(workerData.file);
    writerAdmission(workerData.signal).wait().then(() => {
      db.exec('BEGIN IMMEDIATE');
      parentPort.postMessage('locked');
      const deadline = Date.now() + 5000;
      while (!Atomics.load(idle, 0) && Date.now() < deadline) Atomics.wait(idle, 0, 0, 5);
      Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 20);
      db.exec('ROLLBACK');
      db.close();
    }).catch(error => { throw error; });
  `, { eval: true, workerData: { file: host.file, writerPath, signal } });
  try {
    const finished = once(worker, 'exit');
    assert.equal((await once(worker, 'message'))[0], 'locked');
    const timing = traceTransactions(host.store.db);
    const item = sample('critical-overlap', Date.now(), 15, true);
    item.analysis = analyze(item, []);
    markDeterministic(signal, true);
    try { host.store.saveSample(item); } finally { markDeterministic(signal, false); }
    assert.equal((await finished)[0], 0);
    assert.equal(host.store.latest().id, item.id);
    assert.equal(host.store.getIssues().find(issue => issue.key === 'space:C:').severity, 'critical');
    assert.equal(host.store.db.prepare('PRAGMA quick_check').get().quick_check, 'ok');
    assert.ok(timing[0].waitMs >= 10);
    console.log('PHASE8D ' + JSON.stringify({ kind: 'residual', value: {
      alreadyAdmitted: true, deterministicWaitMs: timing[0].waitMs, deterministicSamplePersisted: true, criticalFindingPersisted: true
    } }));
  } finally { await worker.terminate(); }
});
