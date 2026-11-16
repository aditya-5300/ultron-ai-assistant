const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const { performance } = require('node:perf_hooks');
const { fixture, repo, until, service, seed } = require('./phase8b-fixture.cjs');
const { checkpoint } = require('../lib/health/shadow/store.cjs');

const options = { skip: process.platform !== 'win32' || !process.env.ULTRON_PHASE8B_PYTHON, timeout: 240000 };
const report = (kind, value) => console.log('PHASE8C ' + JSON.stringify({ kind, value }));
const points = require('../docs/phase8c-reference/phase8c_config.json').collector_fault_points;

function isolatedCollector(context) {
  const target = fixture(context);
  fs.cpSync(path.join(repo, 'lib/health'), path.join(target.root, 'lib/health'), { recursive: true });
  fs.mkdirSync(path.join(target.root, 'scripts'));
  for (const file of ['health-worker.cjs', 'collect-health.ps1']) {
    fs.copyFileSync(path.join(repo, 'scripts', file), path.join(target.root, 'scripts', file));
  }
  const patch = (file, search, replacement) => {
    const local = path.join(target.root, file);
    const source = fs.readFileSync(local, 'utf8').replaceAll('\r\n', '\n');
    assert.ok(source.includes(search), 'test insertion point moved: ' + file);
    fs.writeFileSync(local, source.replace(search, replacement));
  };
  const hook = `globalThis.phase8cProbe = point => {
    const fs = require('node:fs');
    const control = require('node:path').join(process.cwd(), 'phase8c-arm');
    if (!fs.existsSync(control) || fs.readFileSync(control, 'utf8') !== point) return;
    fs.writeFileSync(control + '.hit', point);
    Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 60000);
  };\n`;
  patch('scripts/health-worker.cjs', "const fs = require('node:fs');", hook + "const fs = require('node:fs');");
  patch('scripts/health-worker.cjs', 'store.saveSample(sample);', "store.saveSample(sample); globalThis.phase8cProbe('after_sample');");
  patch('scripts/health-worker.cjs', 'shadow?.submit(sample.id);', "globalThis.phase8cProbe('before_dispatch'); shadow?.submit(sample.id);");
  patch('lib/health/shadow/thread.cjs', "const { parentPort, workerData }", hook + "const { parentPort, workerData }");
  patch('lib/health/shadow/client.cjs', 'req.end(body);',
    "if (body) req.once('finish', () => globalThis.phase8cProbe?.('pending_http')); req.end(body);");
  patch('lib/health/shadow/job.cjs', 'timing.httpMs = performance.now() - sent;',
    "globalThis.phase8cProbe?.('after_response'); timing.httpMs = performance.now() - sent;");
  patch('lib/health/shadow/job.cjs', 'timing.persistMs = performance.now() - persisting;',
    "globalThis.phase8cProbe?.('after_commit'); timing.persistMs = performance.now() - persisting;");
  patch('lib/health/shadow/store.cjs', 'finish?.(counts);',
    "finish?.(counts); globalThis.phase8cProbe?.('during_transaction');");
  patch('lib/health/shadow/scheduler.cjs', 'return scheduler({ instrument: true, start:',
    `return scheduler({ now: () => {
      const fs = require('node:fs');
      const file = path.join(root, 'phase8c-clock');
      return fs.existsSync(file) ? Number(fs.readFileSync(file, 'utf8')) : Date.now();
    }, instrument: true, start:`);
  patch('lib/health/shadow/scheduler.cjs', 'settle(result);\n          });',
    "settle(result);\n            require('node:fs').appendFileSync(path.join(process.cwd(), 'phase8c-settled'), '.');\n          });");
  const db = target.store.db;
  const counter = () => db.prepare('SELECT count(*) AS n FROM samples').get().n;
  const lastAttempt = () => {
    const exists = db.prepare("SELECT 1 FROM sqlite_master WHERE name='shadow_runs'").get();
    return exists ? db.prepare('SELECT * FROM shadow_runs ORDER BY id DESC LIMIT 1').get() : null;
  };
  const state = () => target.store.setting('worker', {});
  function start(python) {
    target.store.set('config', { enabled: true, intervalSeconds: 15, retentionDays: 7 });
    return target.spawnLocal(process.execPath, ['scripts/health-worker.cjs', '--background'], {
      ULTRON_INFERENCE_MODE: 'shadow', ULTRON_INFERENCE_PORT: String(python.port), ULTRON_INFERENCE_TOKEN: python.token
    });
  }
  async function nextSample(child, since) {
    const sample = await until(() => counter() > since && state().pid === child.pid && state().status === 'running' &&
      target.store.latest(), 'Windows sample and heartbeat');
    assert.equal(sample.platform, 'win32');
    assert.match(sample.coverage.processes, /^ok:/);
    assert.ok(Array.isArray(sample.analysis.issues));
    return sample;
  }
  async function shutdown(child) {
    target.store.set('config', { ...target.store.config(), enabled: false });
    await until(() => child.exitCode !== null, 'collector shutdown');
    assert.equal(child.exitCode, 0);
  }
  return { ...target, db, start, counter, lastAttempt, nextSample, shutdown };
}

test('Windows collector survives deterministic termination at each of six write and request boundaries', options, async context => {
  const rows = [];
  for (const point of points) {
    const scope = { after(cleanup) { this.cleanup = cleanup; } };
    let host, python;
    try {
      host = isolatedCollector(scope);
      python = await service(host);
      await python.start();
      seed(host.db, 391, Date.now() - 391 * 15000, 15, true);
      const arm = path.join(host.root, 'phase8c-arm');
      fs.writeFileSync(arm, point);
      const count = host.counter();
      let worker = host.start(python);
      await until(() => fs.existsSync(arm + '.hit'), point);
      assert.ok(host.counter() > count, 'deterministic sample must already be durable');
      const sampleId = host.store.latest().id;
      const expectedCommit = point === 'after_commit';
      const visible = host.lastAttempt()?.state === 'SHADOW';
      assert.equal(visible, expectedCommit);
      const namespace = visible ? checkpoint(host.db).replay : null;
      await host.stop(worker);
      if (point === 'pending_http') { await python.stop(); await python.start(); }
      fs.unlinkSync(arm);
      const beforeRestart = host.counter();
      const restarted = performance.now();
      const priorAttempt = host.lastAttempt()?.id || 0;
      worker = host.start(python);
      await host.nextSample(worker, beforeRestart);
      const deterministicMs = performance.now() - restarted;
      const recovered = await until(() => {
        const run = host.lastAttempt();
        return run?.id > priorAttempt && run.state === 'SHADOW' && run;
      }, 'post-crash replay');
      const shadowMs = performance.now() - restarted;
      const actual = checkpoint(host.db);
      assert.ok(actual.replay);
      if (namespace) assert.equal(actual.replay, namespace);
      assert.ok(host.store.getSample(sampleId));
      const owners = host.db.prepare('SELECT count(*) AS n FROM shadow_eligibility').get().n;
      assert.equal(owners, 1);
      assert.equal(JSON.parse(recovered.metrics).hypotheticalInterruptions, 0);
      assert.equal(host.db.prepare('PRAGMA quick_check').get().quick_check, 'ok');
      await host.shutdown(worker);
      rows.push({ point, deterministicCommitted: true, shadowCommitted: expectedCommit, deterministicMs, shadowMs, owners, eligible: 0 });
    } finally { if (scope.cleanup) await scope.cleanup(); }
  }
  report('interruptions', rows);
});

test('actual collector experiences maximum backoff and reconciles a pruned anchor without restart', options, async context => {
  const host = isolatedCollector(context);
  const python = await service(host);
  const clock = path.join(host.root, 'phase8c-clock');
  const settled = path.join(host.root, 'phase8c-settled');
  const settledCount = () => fs.existsSync(settled) ? fs.statSync(settled).size : 0;
  const epoch = Date.now();
  let virtual = 0;
  const control = next => {
    virtual = next;
    fs.writeFileSync(clock, String(virtual));
    host.store.set('resample', Date.now() + 1);
  };
  seed(host.db, 500, epoch - 500 * 15000, 15, true);
  fs.writeFileSync(clock, '0');
  await python.start();
  const worker = host.start(python);
  const started = performance.now();
  const boot = await until(() => host.lastAttempt()?.state === 'SHADOW' && host.lastAttempt(), 'initial success');
  await until(() => settledCount() === 1, 'initial scheduler settlement');
  const original = checkpoint(host.db);
  assert.ok(original.replay);
  await python.stop();
  const rows = [];
  let prior = boot.id;
  for (const [index, next] of [5, 10, 15, 25, 45].entries()) {
    const samples = host.counter();
    control(next * 60000);
    const result = await until(() => {
      const run = host.lastAttempt();
      return run?.id > prior && run.code === 'UNAVAILABLE' && run;
    }, 'refused attempt ' + index);
    await until(() => settledCount() === index + 2, 'failure scheduler settlement');
    prior = result.id;
    assert.ok(host.counter() > samples);
    assert.deepEqual(checkpoint(host.db), original);
    rows.push({ attempt: index + 1, virtualMinutes: virtual / 60000, state: result.state, code: result.code });
  }
  const removed = host.db.prepare('DELETE FROM samples WHERE id=?').run(original.start_id).changes;
  assert.equal(removed, 1);
  await python.start();
  const before = host.counter();
  control(75 * 60000 - 1);
  await host.nextSample(worker, before);
  assert.equal(host.lastAttempt().id, prior);
  control(75 * 60000);
  const recovered = await until(() => {
    const run = host.lastAttempt();
    return run?.id > prior && run.state === 'SHADOW' && run;
  }, 'recovery at maximum backoff');
  assert.equal(recovered.code, 'REPLAY_REBASED');
  assert.notEqual(checkpoint(host.db).replay, original.replay);
  const metrics = JSON.parse(recovered.metrics);
  assert.equal(metrics.hypotheticalInterruptions, 0);
  assert.equal(metrics.scheduler.backoffProbe, true);
  assert.equal(host.db.prepare('PRAGMA quick_check').get().quick_check, 'ok');
  await host.shutdown(worker);
  rows.push({ attempt: 6, virtualMinutes: virtual / 60000, state: recovered.state, code: recovered.code });
  report('outage', { rows, elapsedMs: performance.now() - started, sameCollector: true,
    anchorPruned: true, skipped: metrics.scheduler.skipped, eligible: metrics.hypotheticalInterruptions });
});
