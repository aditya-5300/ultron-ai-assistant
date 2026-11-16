const { test } = require('node:test');
const assert = require('node:assert/strict');
const { Worker } = require('node:worker_threads');
const { once } = require('node:events');
const { performance } = require('node:perf_hooks');
const { setTimeout: wait } = require('node:timers/promises');
const path = require('node:path');
const { fixture, repo, sample, seed, config } = require('./phase8b-fixture.cjs');
const { analyze } = require('../lib/health/core.cjs');
const { migrateObservability, checkpoint } = require('../lib/health/shadow/store.cjs');
const { markDeterministic, writerAdmission, MAX_DEFERRAL_MS } = require('../lib/health/shadow/admission.cjs');
const { traceTransactions } = require('./phase8d-timing.cjs');

const policy = require('../docs/phase8d-reference/phase8d_config.json');
const report = (kind, value) => console.log('PHASE8D ' + JSON.stringify({ kind, value }));
const percentile = (values, factor) => values.length ? [...values].sort((a, b) => a - b)[Math.ceil(values.length * factor) - 1] : null;
const { writer_samples_per_mode: total, writer_interval_ms: interval } = config;

async function workload(context, mode) {
  const target = fixture(context);
  const { db } = target.store;
  const time = Date.now() - 60000;
  seed(db, 400, time - 400 * 15000);
  migrateObservability(db);
  const timing = traceTransactions(db);
  const activity = mode === 'phase8d' ? new SharedArrayBuffer(4) : null;
  const start = Date.now() + 450;
  const worker = new Worker(path.join(repo, 'scripts/phase8d-writer.cjs'), { workerData: {
    file: target.file, start, count: total, mode, activity
  } });
  const result = once(worker, 'message');
  const deterministic = [];
  let lost = 0, critical = 0, busy = 0;
  try {
    for (let index = 0; index < total; index++) {
      const due = start + index * interval;
      if (Date.now() < due) await wait(due - Date.now());
      const item = sample('critical-' + index, time + index, 15, true);
      markDeterministic(activity, true);
      const analysis = performance.now();
      item.analysis = analyze(item, []);
      const persistence = performance.now();
      const stamp = timing.length;
      try { target.store.saveSample(item); }
      catch (error) { lost++; if ([5, 6].includes(error.errcode & 255)) busy++; }
      finally { markDeterministic(activity, false); }
      const persisted = performance.now();
      if (target.store.getIssues().some(issue => issue.key === 'space:C:' && issue.severity === 'critical' && issue.lastSeen === item.time)) critical++;
      deterministic.push({ analysisMs: persistence - analysis, persistenceMs: persisted - persistence,
        overrunMs: Math.max(0, Date.now() - due - interval), ...timing[stamp] });
    }
    const [shadow] = await result;
    assert.equal(db.prepare('PRAGMA quick_check').get().quick_check, 'ok');
    assert.equal(db.prepare("SELECT count(*) AS n FROM samples WHERE id LIKE 'critical-%'").get().n, total);
    assert.equal(lost, 0);
    assert.equal(critical, total);
    if (mode !== 'disabled') assert.equal(shadow.length, total);
    const finished = shadow.filter(row => row.outcome === 'ok').length;
    const shadowBusy = shadow.filter(row => row.outcome === 'busy').length;
    const deferred = shadow.filter(row => row.deferredMs > 0).length;
    return { mode, deterministic, shadow, lost, critical, deterministicBusy: busy, finished, shadowBusy, deferred,
      failed: shadow.length - finished, p95: percentile(deterministic.map(row => row.persistenceMs), .95) };
  } finally { await worker.terminate(); }
}

test('five paired repetitions keep both original cadences and every lock-hold attempt', { timeout: 180000 }, async context => {
  assert.equal(MAX_DEFERRAL_MS, policy.admission_wait_ms);
  assert.equal(config.deterministic_p95_max_added_ms, policy.threshold_ms);
  const rows = [];
  for (let index = 0; index < policy.runs; index++) {
    const modes = index % 2 ? ['phase8d', 'phase8c', 'disabled'] : policy.comparators;
    const results = {};
    for (const mode of modes) results[mode] = await workload(context, mode);
    const added = results.phase8d.p95 - results.disabled.p95;
    rows.push({ repetition: index + 1, results, added, passed: added <= policy.threshold_ms });
  }
  report('benchmark', rows);
});

test('stalled activity hint expires without blocking deterministic commits, and later admits recovery', async context => {
  const { store } = fixture(context);
  migrateObservability(store.db);
  const signal = new SharedArrayBuffer(4);
  const admission = writerAdmission(signal);
  const initial = checkpoint(store.db);
  markDeterministic(signal, true);
  const began = performance.now();
  const deferred = assert.rejects(admission.wait(), { code: 'WRITER_DEFERRED' });
  const item = sample('critical-admission', Date.now(), 15, true);
  item.analysis = analyze(item, []);
  store.saveSample(item);
  assert.equal(store.latest().id, item.id);
  assert.equal(store.getIssues().find(issue => issue.key === 'space:C:').severity, 'critical');
  await deferred;
  const elapsedMs = performance.now() - began;
  assert.deepEqual(checkpoint(store.db), initial);
  const retry = admission.wait();
  await wait(12);
  markDeterministic(signal, false);
  const recoveryMs = await retry;
  assert.ok(recoveryMs > 0);
  report('coordinator', { elapsedMs, recoveryMs, checkpointUnchanged: true, deterministicSamplePersisted: true });
});
