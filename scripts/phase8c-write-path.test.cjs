const assert = require('node:assert/strict');
const { test } = require('node:test');
const { DatabaseSync } = require('node:sqlite');
const { performance } = require('node:perf_hooks');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const { Worker } = require('node:worker_threads');
const { once } = require('node:events');
const { fixture, seed } = require('./phase8b-fixture.cjs');
const { exportReplay } = require('../lib/health/shadow/export.cjs');
const candidate = require('../lib/health/shadow/store.cjs');
const { hash } = require('../lib/health/shadow/contract.cjs');

function measure(store, label) {
  const db = new DatabaseSync(':memory:');
  try {
    db.exec('CREATE TABLE samples(id TEXT PRIMARY KEY)');
    db.exec("INSERT INTO samples VALUES ('start'),('end')");
    store.migrateObservability(db);
    const start = Date.now() - 5400000;
    const namespace = hash('fixture');
    const replay = { startId: 'start', end: start + 5400000,
      request: { replay_id: namespace, telemetry: { records: [{ sample_id: 'start' }, { sample_id: 'end' }] } } };
    const findings = Array.from({ length: 600 }, (_, index) => {
      const active = index % 2 === 0;
      return { finding_id: hash('finding', Math.floor(index / 2)), event_id: hash('event', index),
        decision_at: new Date(start + index * 3000).toISOString(), subsystem: 'process',
        transition: active ? 'OPEN' : 'CLOSE', state: active ? 'active' : 'closed',
        reason: active ? null : 'fresh_recovery', evidence: { quality: 'fresh' } };
    });
    let locked, lastHold;
    const execute = db.exec.bind(db);
    db.exec = sql => {
      const result = execute(sql);
      if (sql === 'BEGIN IMMEDIATE') locked = performance.now();
      if (sql === 'COMMIT') lastHold = performance.now() - locked;
      return result;
    };
    function run(finish) {
      const began = performance.now();
      const result = store.consume(db, replay, findings, store.checkpoint(db), finish);
      return { elapsedMs: performance.now() - began, heldMs: lastHold, result };
    }
    const baseline = store.checkpoint(db);
    assert.throws(() => run(result => {
      store.recordRun(db, 'SHADOW', 'OK', 0, result);
      throw new Error('abort just before commit');
    }), /abort just before commit/);
    assert.deepEqual(store.checkpoint(db), baseline);
    for (const table of ['shadow_runs', 'shadow_findings', 'shadow_consumed', 'shadow_eligibility', 'shadow_transitions', 'shadow_attempts', 'shadow_status']) {
      assert.equal(db.prepare(`SELECT count(*) AS n FROM ${table}`).get().n, 0);
    }
    assert.equal(run().result.openings, 300);
    assert.equal(db.prepare('SELECT count(*) AS n FROM shadow_eligibility').get().n, 300);
    const repeats = Array.from({ length: 30 }, () => run());
    assert.ok(repeats.every(item => item.result.openings === 0 && item.result.repeated === 600));
    assert.equal(db.prepare('PRAGMA quick_check').get().quick_check, 'ok');
    return { label, repeats: repeats.map(({ elapsedMs, heldMs }) => ({ elapsedMs, heldMs })) };
  } finally { db.close(); }
}

test('moving preparation outside BEGIN preserves rollback and shortens repeat work inside the transaction', () => {
  const livePath = require.resolve('../lib/health/shadow/store.cjs');
  const snapshot = path.join(__dirname, '../docs/phase8c-reference/phase8a-store.cjs.txt');
  const historical = new Module(livePath, module);
  historical.filename = livePath;
  historical._compile(fs.readFileSync(snapshot, 'utf8'), livePath);
  const results = [measure(historical.exports, 'phase8b_store'), measure(candidate, 'phase8c_store')];
  console.log('PHASE8C ' + JSON.stringify({ kind: 'writePath', value: results }));
});

test('a separate writer forces repeated bounded SQLITE_BUSY without advancing the checkpoint', async context => {
  const target = fixture(context);
  const db = target.store.db;
  seed(db, 400, Date.now() - 400 * 15000);
  candidate.migrateObservability(db);
  db.exec('PRAGMA busy_timeout=50');
  const saved = candidate.checkpoint(db);
  const replay = exportReplay(db, 'seed-399', saved.installation, saved);
  const locker = new Worker(`
    const { parentPort, workerData } = require('node:worker_threads');
    const { DatabaseSync } = require('node:sqlite');
    const db = new DatabaseSync(workerData);
    db.exec('BEGIN IMMEDIATE');
    parentPort.postMessage('locked');
    parentPort.once('message', () => { db.exec('ROLLBACK'); db.close(); parentPort.postMessage('released'); });
  `, { eval: true, workerData: target.file });
  try {
    assert.equal((await once(locker, 'message'))[0], 'locked');
    const waits = [];
    for (let attempt = 0; attempt < 3; attempt++) {
      const start = performance.now();
      assert.throws(() => candidate.consume(db, replay, [], saved), error => [5, 6].includes(error.errcode & 255));
      waits.push(performance.now() - start);
      assert.deepEqual(candidate.checkpoint(db), saved);
    }
    const released = once(locker, 'message');
    locker.postMessage('release');
    assert.equal((await released)[0], 'released');
    assert.equal(candidate.consume(db, replay, [], saved).openings, 0);
    assert.equal(candidate.checkpoint(db).asof, replay.end);
    assert.equal(db.prepare('PRAGMA quick_check').get().quick_check, 'ok');
    console.log('PHASE8C ' + JSON.stringify({ kind: 'busyStress', value: { busy: waits.length, waitsMs: waits, recovered: true } }));
  } finally { await locker.terminate(); }
});
