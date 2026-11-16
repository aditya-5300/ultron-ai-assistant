const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { once } = require('node:events');
const { Worker } = require('node:worker_threads');
const { DatabaseSync } = require('node:sqlite');
const { performance } = require('node:perf_hooks');
const { createHash } = require('node:crypto');
const { setTimeout: wait } = require('node:timers/promises');
const { analyze } = require('../lib/health/core.cjs');
const { migrateObservability, checkpoint } = require('../lib/health/shadow/store.cjs');
const { exportReplay } = require('../lib/health/shadow/export.cjs');
const { createClient } = require('../lib/health/shadow/client.cjs');
const { verifyResponse } = require('../lib/health/shadow/contract.cjs');
const { scheduler } = require('../lib/health/shadow/scheduler.cjs');
const { config, repo, fixture, until, service, request: rawRequest, sample, seed, evidence } = require('./phase8b-fixture.cjs');

const serviceRequired = { skip: !process.env.ULTRON_PHASE8B_PYTHON, timeout: 180000 };

test('actual Windows collector: refusal, clean shutdown, abrupt restart and persisted recovery', {
  ...serviceRequired, skip: !process.env.ULTRON_PHASE8B_PYTHON || process.platform !== 'win32'
}, async context => {
  const target = fixture(context);
  const { root, store } = target;
  for (const directory of ['lib/health', 'scripts']) {
    fs.mkdirSync(path.join(root, directory), { recursive: true });
  }
  fs.cpSync(path.join(repo, 'lib/health'), path.join(root, 'lib/health'), { recursive: true });
  for (const file of ['health-worker.cjs', 'collect-health.ps1']) {
    fs.copyFileSync(path.join(repo, 'scripts', file), path.join(root, 'scripts', file));
  }
  seed(store.db, 391, Date.now() - 391 * 15000, 15, true);
  const python = await service(target);
  const rows = [];
  const count = () => store.db.prepare('SELECT count(*) AS n FROM samples').get().n;
  const heartbeat = () => store.setting('worker', {});
  const shadow = () => store.db.prepare('SELECT * FROM shadow_status WHERE id=1').get();
  const attempts = () => store.db.prepare('SELECT max(id) AS id FROM shadow_attempts').get().id;
  function start() {
    store.set('config', { enabled: true, intervalSeconds: config.restart_cadence_seconds, retentionDays: 7 });
    return target.spawnLocal(process.execPath, ['scripts/health-worker.cjs', '--background'], {
      ULTRON_INFERENCE_MODE: 'shadow', ULTRON_INFERENCE_PORT: String(python.port), ULTRON_INFERENCE_TOKEN: python.token
    });
  }
  async function collected(previous, worker) {
    await until(() => count() > previous && heartbeat().pid === worker.pid && heartbeat().status === 'running', 'collector sample');
    const latest = store.latest();
    assert.equal(latest.platform, 'win32');
    assert.match(latest.coverage.processes || '', /^ok:/);
    assert.ok(latest.analysis && Array.isArray(latest.analysis.issues));
  }
  let before = count(), started = performance.now();
  let worker = start();
  await collected(before, worker);
  const initialCollection = performance.now() - started;
  await until(() => {
    try { return shadow()?.code === 'UNAVAILABLE'; } catch { return false; }
  }, 'refused Python connection');
  rows.push({ scenario: 'startup_without_python', collection_ms: initialCollection, shadow_ms: performance.now() - started, outcome: 'UNAVAILABLE' });
  store.set('config', { ...store.config(), enabled: false });
  await until(() => worker.exitCode !== null, 'clean stop');
  assert.equal(worker.exitCode, 0);
  assert.equal(heartbeat().status, 'stopped');
  assert.equal(fs.existsSync(path.join(root, '.ultron', 'health-worker.lock')), false);
  await python.start();
  before = count(); started = performance.now();
  let previous = attempts();
  worker = start();
  await collected(before, worker);
  const collection = performance.now() - started;
  await until(() => attempts() > previous && shadow()?.state === 'SHADOW', 'shadow checkpoint');
  rows.push({ scenario: 'clean_restart', collection_ms: collection, shadow_ms: performance.now() - started, outcome: 'SHADOW' });
  const durable = checkpoint(store.db);
  assert.ok(durable.replay);
  const ownership = store.db.prepare('SELECT count(*) AS n FROM shadow_eligibility').get().n;
  assert.ok(ownership > 0, 'synthetic historical ownership should be persisted');
  for (let repeat = 0; repeat < 2; repeat++) {
    await target.stop(worker);
    assert.equal(fs.existsSync(path.join(root, '.ultron', 'health-worker.lock')), true);
    if (repeat === 0) { await python.stop(); await python.start(); }
    before = count(); previous = attempts(); started = performance.now();
    worker = start();
    await collected(before, worker);
    const collection = performance.now() - started;
    await until(() => attempts() > previous && shadow()?.state === 'SHADOW', 'restart replay');
    assert.equal(checkpoint(store.db).replay, durable.replay);
    assert.equal(store.db.prepare('SELECT count(*) AS n FROM shadow_eligibility').get().n, ownership);
    rows.push({ scenario: repeat ? 'second_abrupt_restart' : 'abrupt_restart_after_shadow_commit',
      collection_ms: collection, shadow_ms: performance.now() - started, outcome: 'SHADOW' });
  }
  store.set('config', { ...store.config(), enabled: false });
  await until(() => worker.exitCode !== null, 'final clean stop');
  assert.equal(store.db.prepare('PRAGMA quick_check').get().quick_check, 'ok');
  evidence('collector', { elapsedMs: rows.reduce((sum, row) => sum + row.shadow_ms, 0), rows, durableHistoricalOwners: ownership,
    eligible: store.db.prepare("SELECT count(*) AS n FROM shadow_eligibility WHERE reason='ELIGIBLE'").get().n });
});

test('authenticated HTTP accepts bounded and exact-size requests, and rejects oversized requests', serviceRequired, async context => {
  const target = fixture(context);
  const { db } = target.store;
  migrateObservability(db);
  const count = 1800, step = 30;
  const end = Math.floor(Date.now() / 1000) * 1000 - 60000;
  seed(db, count, end - (count - 1) * step * 1000, step);
  const saved = checkpoint(db);
  const replay = exportReplay(db, 'seed-1799', saved.installation, { ...saved, start_id: 'seed-360' });
  assert.equal(replay.request.telemetry.records.length, 1440);
  db.exec('DELETE FROM samples');
  seed(db, count, end - (count - 1) * 15000, 15);
  const bounded = exportReplay(db, 'seed-1799', saved.installation, { ...saved, start_id: 'seed-0' });
  assert.equal(bounded.request.telemetry.records.length, count);
  const python = await service(target);
  await python.start();
  const client = createClient(python);
  const original = JSON.stringify(bounded.request);
  const cap = config.http_body_limit_bytes;
  const rows = [];
  for (const [label, request, size] of [['natural_max_rows', bounded.request, original.length],
    ['near_cap_json_whitespace', bounded.request, cap - 8192], ['exact_cap_json_whitespace', bounded.request, cap],
    ['over_cap_json_whitespace', bounded.request, cap + 1], ['twelve_hour_history', replay.request, JSON.stringify(replay.request).length]]) {
    const json = JSON.stringify(request);
    assert.ok(size >= json.length);
    const body = json.padEnd(size, ' ');
    const started = performance.now();
    let result = 'accepted';
    if (size > cap) {
      await assert.rejects(client.infer(body), { code: 'CONTRACT_REJECTED' });
      result = 'rejected';
    } else {
      verifyResponse(await client.infer(body), request);
    }
    rows.push({ scenario: label, bytes: Buffer.byteLength(body), rows: request.telemetry.records.length,
      duration_ms: performance.now() - started, result });
  }
  const response = await rawRequest(python.port, '/v1/infer', original.padEnd(cap + 1), python.token);
  assert.equal(response.status, 413);
  assert.deepEqual(JSON.parse(response.body), { error: 'REQUEST_TOO_LARGE' });
  evidence('body', rows);
});

test('production scheduler exercises max backoff with a virtual clock and real service recovery', serviceRequired, async context => {
  const target = fixture(context);
  const { db } = target.store;
  seed(db, 400, Date.now() - 400 * 15000, 15, true);
  const python = await service(target);
  let now = 0, pending, thread;
  const outcome = () => new Promise(resolve => { pending = resolve; });
  const runner = scheduler({ now: () => now, instrument: true, start() {
    thread = new Worker(path.join(repo, 'lib/health/shadow/thread.cjs'), {
      env: {}, workerData: { path: target.file, config: { port: python.port, token: python.token } }
    });
    thread.on('message', result => pending(result));
    return thread;
  } });
  context.after(() => runner.close());
  const rows = [];
  for (const [attempt, delay] of [300000, 300000, 600000, 1200000, 1800000].entries()) {
    const completed = outcome();
    assert.ok(runner.submit('seed-399'));
    assert.equal(runner.submit('seed-399'), false);
    const failure = await completed;
    assert.equal(failure.code, 'UNAVAILABLE');
    now += delay - 1;
    assert.equal(runner.submit('seed-399'), false);
    now++;
    rows.push({ attempt: attempt + 1, code: failure.code, backoff_ms: delay, simulated_elapsed_ms: now, skipped: 2 });
  }
  await python.start();
  const completed = outcome(), started = performance.now();
  assert.ok(runner.submit('seed-399'));
  const recovered = await completed;
  assert.ok(recovered.ok, JSON.stringify(recovered));
  assert.equal(recovered.hypotheticalInterruptions, 0);
  assert.equal(db.prepare('SELECT count(*) AS n FROM samples').get().n, 400);
  rows.push({ attempt: 6, code: 'OK', backoff_ms: 0, simulated_elapsed_ms: now, skipped: 0,
    restoration_to_success_ms: performance.now() - started });
  await runner.close();
  evidence('outage', rows);
});

test('independent isolated replays agree on exact finding IDs, transitions and eligibility', serviceRequired, async context => {
  const serviceRoot = fixture(context);
  const python = await service(serviceRoot);
  await python.start();
  const epoch = Date.UTC(2025, 0, 1), installation = 'phase8b-reproducible-test-installation';
  const outcomes = [];
  for (let repeat = 0; repeat < 2; repeat++) {
    const { db } = fixture(context).store;
    migrateObservability(db);
    db.prepare('UPDATE shadow_checkpoint SET installation=?').run(installation);
    seed(db, 1741, epoch, 30, true);
    const decisions = [];
    for (const index of [180, 360, 720, 1080, 1440, 1740, 1740]) {
      const saved = checkpoint(db);
      const replay = exportReplay(db, 'seed-' + index, installation, saved);
      const findings = verifyResponse(await createClient(python).infer(JSON.stringify(replay.request)), replay.request);
      const { consume } = require('../lib/health/shadow/store.cjs');
      const result = consume(db, replay, findings, saved);
      const eligible = db.prepare('SELECT finding,event,replay,policy,reason FROM shadow_eligibility ORDER BY finding').all();
      decisions.push({ index, findings, result, eligible });
      if (result.rotated || decisions.length === 7) assert.equal(result.hypotheticalInterruptions, 0);
    }
    outcomes.push(decisions);
    if (!repeat) { await python.stop(); await python.start(); }
  }
  assert.deepEqual(outcomes[0], outcomes[1]);
  const decision = outcomes[0];
  const counts = decision.reduce((total, row) => {
    total.raw += row.result.rawTransitions;
    total.repeated += row.result.repeated;
    total.eligible += row.result.hypotheticalInterruptions;
    return total;
  }, { raw: 0, repeated: 0, eligible: 0 });
  assert.ok(counts.eligible > 0 && counts.repeated > 0);
  evidence('exactReplay', { ...counts, runs: 2, requestsPerRun: decision.length, duplicateHypotheticalOpenings: 0,
    digest: createHash('sha256').update(JSON.stringify(decision)).digest('hex') });
});

test('independent WAL writers preserve critical findings within the predeclared p95 budget', { timeout: 45000 }, async context => {
  const results = [];
  const { writer_samples_per_mode: total, writer_interval_ms: interval } = config;
  for (const mode of ['disabled', 'shadow']) {
    const target = fixture(context);
    const { db } = target.store;
    const time = Date.now() - 60000;
    seed(db, 400, time - 400 * 15000);
    migrateObservability(db);
    const start = Date.now() + 450;
    const worker = new Worker(path.join(repo, 'scripts/phase8b-writer.cjs'), { workerData: {
      file: target.file, start, count: total, enabled: mode === 'shadow'
    } });
    context.after(() => worker.terminate());
    const completed = once(worker, 'message');
    const samples = [], overruns = [];
    let lost = 0, critical = 0;
    for (let index = 0; index < total; index++) {
      const due = start + index * interval;
      if (Date.now() < due) await wait(due - Date.now());
      const item = sample('critical-' + index, time + index, 15, true);
      const analysis = performance.now();
      item.analysis = analyze(item, []);
      const persistence = performance.now();
      try { target.store.saveSample(item); } catch { lost++; }
      const persisted = performance.now();
      if (target.store.getIssues().some(issue => issue.key === 'space:C:' && issue.severity === 'critical' && issue.lastSeen === item.time)) critical++;
      samples.push({ analysis: persistence - analysis, persistence: persisted - persistence });
      overruns.push(Math.max(0, Date.now() - due - interval));
    }
    const [shadow] = await completed;
    assert.equal(db.prepare('PRAGMA quick_check').get().quick_check, 'ok');
    assert.equal(db.prepare("SELECT count(*) AS n FROM samples WHERE id LIKE 'critical-%'").get().n, total);
    assert.equal(lost, 0);
    assert.equal(critical, total);
    results.push({ mode, samples, overruns, lost, critical, shadow });
  }
  const p95 = values => [...values].sort((a, b) => a - b)[Math.ceil(values.length * .95) - 1];
  const delta = p95(results[1].samples.map(row => row.persistence)) - p95(results[0].samples.map(row => row.persistence));
  evidence('concurrent', { delta, gate: delta <= config.deterministic_p95_max_added_ms, results });
  assert.ok(results[1].shadow.ok > 0);
});
