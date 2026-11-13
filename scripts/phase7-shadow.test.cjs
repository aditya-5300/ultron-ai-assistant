const assert = require('node:assert/strict');
const { test } = require('node:test');
const { EventEmitter, once } = require('node:events');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const { DatabaseSync } = require('node:sqlite');
const { Worker } = require('node:worker_threads');
const { randomBytes } = require('node:crypto');
const { setTimeout: wait } = require('node:timers/promises');
const { createStore } = require('../lib/health/core.cjs');
const { VERSION, POLICY, FINGERPRINTS, SCOPE, LIMITS, verifyResponse, hash } = require('../lib/health/shadow/contract.cjs');
const { exportReplay } = require('../lib/health/shadow/export.cjs');
const { migrate, checkpoint, consume, clearShadow } = require('../lib/health/shadow/store.cjs');
const { createClient } = require('../lib/health/shadow/client.cjs');
const { createJob } = require('../lib/health/shadow/job.cjs');
const { scheduler, createShadowScheduler } = require('../lib/health/shadow/scheduler.cjs');

const token = randomBytes(32).toString('base64url');
const meta = { api_version: VERSION, telemetry_schema_version: VERSION, feature_schema_version: VERSION,
  feature_fingerprints: FINGERPRINTS, policy_version: POLICY, detection_scope: SCOPE.detection,
  forecasting_scope: SCOPE.forecasting, ...LIMITS };
const origin = Date.now() - 3 * 3600000;
function setup(context) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'ultron-phase7-'));
  const store = createStore(root);
  context.after(() => {
    store.db.close();
    for (const file of fs.readdirSync(path.join(root, '.ultron'))) fs.unlinkSync(path.join(root, '.ultron', file));
    fs.rmdirSync(path.join(root, '.ultron'));
    fs.rmdirSync(root);
  });
  return { root, store, db: store.db };
}
function sample(index) {
  const time = new Date(origin + index * 30000).toISOString();
  const stalls = index >= 125 && index < 146 ? 2 : index >= 146 && index < 185 ? 5 : 0;
  return { id: 'sample-' + index, time, intervalSeconds: 30, cpuPct: 38, memory: { usedPct: 61 },
    volumes: [{ drive: 'C:', freePct: 55 }], deepTime: new Date(origin + Math.floor(index / 10) * 300000).toISOString(),
    coverage: { processes: 'ok: counters' }, processes: Array.from({ length: 5 }, (_, slot) =>
      ({ name: 'private fixture process', responding: slot >= stalls })),
    analysis: { issues: [], risks: [] } };
}
function seed(db, count) {
  const insert = db.prepare('INSERT INTO samples VALUES (?,?,?)');
  db.exec('BEGIN');
  for (let index = 0; index < count; index++) {
    const row = sample(index);
    insert.run(row.id, row.time, JSON.stringify(row));
  }
  db.exec('COMMIT');
}
function envelope(request, findings) {
  return { api_version: VERSION, telemetry_schema_version: VERSION, feature_schema_version: VERSION,
    feature_fingerprints: FINGERPRINTS, policy_version: POLICY, request_id: request.request_id, replay_id: request.replay_id,
    as_of: request.telemetry.as_of, advisory_only: true, findings, process_raw_qualifying_rows: 0,
    logical_open_count: findings.filter(row => row.transition === 'OPEN').length };
}
function detection(request, index, firstIndex = index, transition = 'OPEN') {
  const row = request.telemetry.records[index];
  const first = request.telemetry.records[firstIndex];
  const findingId = hash(request.replay_id, 'process', (Date.parse(first.sample_time) / 1000).toFixed(3)
    .replace(/0+$/, '').replace(/\.$/, '.0'));
  return { finding_id: findingId, event_id: hash(findingId, row.sample_id, transition),
    subsystem: 'process', finding_type: 'DETECTION', transition, state: transition === 'CLOSE' ? 'closed' : 'active',
    severity: 'advisory', first_at: first.sample_time, decision_at: row.sample_time, latest_evidence_at: row.sample_time,
    definition_version: 'phase45c-v1', source: 'reduced_hybrid', evidence: { available: true,
      reading: row.metrics.selected_process_nonresponding_count.value, quality: 'fresh', independent_confirmations: null,
      score: null, score_type: null }, horizon_seconds: null, minimum_useful_lead_seconds: null,
    target_window_end: null, reason: transition === 'CLOSE' ? 'fresh_recovery' : null };
}
async function server(context, handler) {
  const instance = http.createServer(handler).listen(0, '127.0.0.1');
  await once(instance, 'listening');
  context.after(() => new Promise(resolve => { instance.close(resolve); instance.closeAllConnections(); }));
  return instance.address().port;
}
function json(res, data) { res.writeHead(200, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(data)); }

test('disabled by default and migrations are additive, atomic, and idempotent', context => {
  assert.equal(createShadowScheduler('.', {}), null);
  assert.equal(createShadowScheduler('.', { ULTRON_INFERENCE_MODE: 'enabled' }), null);
  const { db, store } = setup(context);
  store.saveSample(sample(0));
  assert.equal(db.prepare('PRAGMA user_version').get().user_version, 0);
  db.exec('CREATE TABLE shadow_attempts (unexpected TEXT)');
  assert.throws(() => migrate(db));
  assert.equal(db.prepare('PRAGMA user_version').get().user_version, 0);
  assert.equal(db.prepare("SELECT 1 FROM sqlite_master WHERE name='shadow_status'").get(), undefined);
  db.exec('DROP TABLE shadow_attempts');
  migrate(db);
  const identity = checkpoint(db).installation;
  migrate(db);
  assert.equal(checkpoint(db).installation, identity);
  assert.equal(store.latest().id, 'sample-0');
  store.clear();
  assert.equal(store.latest(), null);
  assert.notEqual(checkpoint(db).installation, identity);
});

test('export uses committed bounds, real cadence, warmup, privacy, and cached time', context => {
  const { db } = setup(context);
  seed(db, 230);
  migrate(db);
  const saved = checkpoint(db);
  assert.throws(() => exportReplay(db, 'sample-179', saved.installation, saved), { code: 'INSUFFICIENT_HISTORY' });
  const replay = exportReplay(db, 'sample-180', saved.installation, saved);
  const { records, as_of: end, window_start: start } = replay.request.telemetry;
  assert.equal(records.length, 181);
  assert.equal(start, sample(0).time);
  assert.equal(end, sample(180).time);
  assert.equal(records[0].elapsed_seconds, null);
  assert.equal(records[1].elapsed_seconds, 30);
  assert.equal(records[1].metrics.disk_free_pct.observation_time, sample(0).time);
  assert.equal(records[1].metrics.disk_free_pct.quality, 'cached');
  assert.ok(!JSON.stringify(replay.request).includes('private fixture'));
  assert.throws(() => exportReplay(db, 'sample-180', saved.installation, saved, origin), { code: 'INVALID_TELEMETRY' });
  const row = sample(5);
  row.coverage = {};
  row.volumes = [];
  db.prepare('UPDATE samples SET data=? WHERE id=?').run(JSON.stringify(row), row.id);
  const missing = exportReplay(db, 'sample-180', saved.installation, saved).request.telemetry.records[5].metrics;
  assert.equal(missing.disk_free_pct.value, null);
  assert.equal(missing.selected_process_nonresponding_count.value, null);
});

test('consumption survives restart, overlaps, conflict rollback, rebase and recurrence', context => {
  const { db, root } = setup(context);
  seed(db, 300);
  migrate(db);
  const initial = checkpoint(db);
  const replay = exportReplay(db, 'sample-180', initial.installation, initial);
  const open = detection(replay.request, 170);
  const parsed = verifyResponse(envelope(replay.request, [open]), replay.request);
  assert.equal(consume(db, replay, parsed, initial).openings, 1);
  const another = new DatabaseSync(path.join(root, '.ultron', 'health.sqlite'));
  try {
    migrate(another);
    const saved = checkpoint(another);
    assert.equal(consume(another, replay, parsed, saved).openings, 0);
    assert.throws(() => consume(another, replay, [], saved), { code: 'CONFLICTING_HISTORY' });
    assert.throws(() => consume(another, replay, parsed, initial), { code: 'STALE_RESPONSE' });
    const next = exportReplay(another, 'sample-200', saved.installation, saved);
    assert.equal(next.request.replay_id, replay.request.replay_id);
    const close = detection(next.request, 190, 170, 'CLOSE');
    const recurrence = detection(next.request, 195);
    const broken = { ...recurrence, event_id: close.event_id };
    const before = checkpoint(another);
    assert.throws(() => consume(another, next, [open, close, broken], before), { code: 'CONFLICTING_EVENT' });
    assert.deepEqual(checkpoint(another), before);
    assert.equal(another.prepare("SELECT state FROM shadow_findings WHERE id=?").get(open.finding_id).state, 'active');
    assert.equal(consume(another, next, verifyResponse(envelope(next.request, [open, close, recurrence]), next.request), before).openings, 1);
    another.prepare('DELETE FROM samples WHERE id=?').run(replay.startId);
    const previous = checkpoint(another);
    const rotated = exportReplay(another, 'sample-250', previous.installation, previous);
    const historical = detection(rotated.request, 170);
    assert.equal(consume(another, rotated, [historical], previous).openings, 0);
    assert.equal(another.prepare('SELECT state FROM shadow_findings WHERE id=?').get(recurrence.finding_id).state, 'superseded');
    assert.equal(consume(another, rotated, [historical], checkpoint(another)).openings, 0);
  } finally { another.close(); }
  clearShadow(db);
  assert.equal(db.prepare('SELECT count(*) AS count FROM shadow_findings').get().count, 0);
});

test('response rejects future, scope escalation, unsafe fields and inconsistent evidence', context => {
  const { db } = setup(context);
  seed(db, 181);
  const { request } = exportReplay(db, 'sample-180', 'fixture', null);
  const opening = detection(request, 170);
  for (const change of [{ subsystem: 'battery' }, { decision_at: new Date().toISOString() }, { command: 'fixture' },
    { finding_id: 'unknown' }, { transition: 'CLOSE' }, { evidence: { ...opening.evidence, reading: 24 } }]) {
    assert.throws(() => verifyResponse(envelope(request, [{ ...opening, ...change }]), request), { code: 'INVALID_RESPONSE' });
  }
  assert.throws(() => verifyResponse({ ...envelope(request, [opening]), advisory_only: false }, request), { code: 'INVALID_RESPONSE' });
});

test('HTTP handshake fails closed; no redirect, token logging, or partial response acceptance', async context => {
  let posts = 0;
  let mode = 'mismatch';
  const port = await server(context, (req, res) => {
    assert.equal(req.headers['x-ultron-service-token'], token);
    if (req.method === 'POST') posts++;
    if (mode === 'mismatch') return json(res, { ...meta, policy_version: 'unknown' });
    if (mode === 'redirect') { res.writeHead(302, { Location: 'http://example.test/' }); return res.end(); }
    if (mode === 'auth') { res.writeHead(401); return res.end(token); }
    if (mode === 'timeout') return;
    if (req.method === 'GET') return json(res, meta);
    if (mode === 'partial') { res.writeHead(200, { 'Content-Type': 'application/json', 'Content-Length': 500 }); res.end('{'); }
  });
  const client = createClient({ port, token, totalMs: 120 });
  for (const [phase, code] of [['mismatch', 'INCOMPATIBLE'], ['redirect', 'REDIRECT_REJECTED'],
    ['auth', 'AUTH_REJECTED'], ['timeout', 'TIMEOUT']]) {
    mode = phase;
    await assert.rejects(client.infer('{}'), { message: code });
    assert.equal(posts, 0);
  }
  mode = 'partial';
  await assert.rejects(client.infer('{}'));
  assert.equal(posts, 1);
});

test('unavailable service and SQLite contention leave deterministic telemetry untouched', async context => {
  const { db, root, store } = setup(context);
  seed(db, 181);
  const port = await server(context, (_req, res) => { res.writeHead(503); res.end(); });
  const job = createJob(db, { port, token });
  assert.equal((await job('sample-180')).code, 'UNAVAILABLE');
  assert.equal(db.prepare('SELECT code FROM shadow_status').get().code, 'UNAVAILABLE');
  const writer = new DatabaseSync(path.join(root, '.ultron', 'health.sqlite'));
  db.exec('PRAGMA busy_timeout=10');
  try {
    writer.exec('BEGIN IMMEDIATE');
    assert.throws(() => consume(db, exportReplay(db, 'sample-180', checkpoint(db).installation, checkpoint(db)), [], checkpoint(db)));
    assert.equal((await job('sample-180')).code, 'UNAVAILABLE');
    writer.exec('ROLLBACK');
    store.saveSample(sample(181));
    assert.equal(store.latest().id, 'sample-181');
    assert.deepEqual(store.getIssues(), []);
  } finally { writer.close(); }
});

test('single outstanding job, circuit backoff, recovery and disabled shutdown', () => {
  const worker = new EventEmitter();
  worker.postMessage = () => {};
  worker.terminate = async () => {};
  let now = 0;
  const queue = scheduler({ start: () => worker, now: () => now, spacingMs: 100 });
  for (let attempt = 0; attempt < 3; attempt++) {
    assert.ok(queue.submit('sample'));
    assert.equal(queue.submit('next'), false);
    worker.emit('message', { ok: false, transient: true });
    now += 100;
  }
  assert.equal(queue.submit('backoff'), false);
  now += 100;
  assert.ok(queue.submit('probe'));
  worker.emit('message', { ok: true });
  now += 100;
  assert.ok(queue.submit('resumed'));
  queue.close();
  assert.equal(queue.submit('closed'), false);
});

test('real Python replay with overlap and durable restart', { skip: !process.env.ULTRON_PHASE7_FIXTURE_PORT }, async context => {
  const { db, root, store } = setup(context);
  seed(db, 245);
  store.saveSample(sample(245));
  const port = Number(process.env.ULTRON_PHASE7_FIXTURE_PORT);
  const config = { port, token: process.env.ULTRON_INFERENCE_TOKEN };
  async function runThread(...ids) {
    const worker = new Worker(path.join(__dirname, '../lib/health/shadow/thread.cjs'),
      { env: {}, workerData: { path: path.join(root, '.ultron', 'health.sqlite'), config } });
    try {
      const outcomes = [];
      for (const id of ids) {
        const response = once(worker, 'message');
        worker.postMessage(id);
        outcomes.push((await response)[0]);
      }
      return outcomes;
    } finally { await worker.terminate(); }
  }
  const results = await runThread('sample-210');
  const ack = process.env.ULTRON_PHASE7_RESTART_ACK;
  if (ack) {
    console.log('PHASE7_RESTART_LISTENER');
    for (let attempt = 0; !fs.existsSync(ack) && attempt < 100; attempt++) await wait(100);
    assert.ok(fs.existsSync(ack), 'fixture listener did not restart');
  }
  results.push(...await runThread('sample-245', 'sample-245'));
  assert.ok(results.every(result => result.ok), JSON.stringify(results));
  const processFindings = db.prepare("SELECT id FROM shadow_findings WHERE subsystem='process'").all();
  assert.equal(processFindings.length, 1);
  const transitions = db.prepare('SELECT data FROM shadow_transitions WHERE finding=? ORDER BY at').all(processFindings[0].id)
    .map(row => JSON.parse(row.data));
  assert.equal(transitions.filter(row => row.transition === 'OPEN').length, 1);
  assert.equal(transitions.find(row => row.transition === 'OPEN').finding_type, 'FORECAST');
  assert.ok(transitions.some(row => row.transition === 'ESCALATE' && row.finding_type === 'DETECTION'));
  assert.ok(transitions.some(row => row.transition === 'CLOSE'));
  assert.equal(results[2].newEvents, 0);
  assert.ok(results.every(row => row.hypotheticalInterruptions === 0));
  assert.equal(store.latest().id, 'sample-245');
  console.log(JSON.stringify({ timings: results, duplicateLogicalOpenings: 0 }));
});
