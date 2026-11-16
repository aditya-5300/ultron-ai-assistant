const assert = require('node:assert/strict');
const { test } = require('node:test');
const { DatabaseSync } = require('node:sqlite');
const { performance } = require('node:perf_hooks');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { createStore, analyze } = require('../lib/health/core.cjs');
const { exportReplay } = require('../lib/health/shadow/export.cjs');
const { migrate, migrateObservability, checkpoint, consume } = require('../lib/health/shadow/store.cjs');
const { handleShadow } = require('../lib/health/shadow/observe.cjs');
const { createJob } = require('../lib/health/shadow/job.cjs');
const { hash } = require('../lib/health/shadow/contract.cjs');
const config = require('../docs/phase8-reference/phase8_config.json');

const report = (kind, data) => console.log('PHASE8 ' + JSON.stringify({ kind, data }));
const base = Math.floor(Date.now() / 86400000) * 86400000 - 4 * 86400000;
function fixture(context) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'ultron-phase8-'));
  const store = createStore(root);
  context.after(() => {
    store.db.close();
    const directory = path.join(root, '.ultron');
    for (const name of fs.readdirSync(directory)) fs.unlinkSync(path.join(directory, name));
    fs.rmdirSync(directory);
    fs.rmdirSync(root);
  });
  return { root, store, db: store.db, file: path.join(root, '.ultron', 'health.sqlite') };
}
function sample(index, step = 30, gap = 0) {
  const time = new Date(base + (index * step + gap) * 1000).toISOString();
  return { id: `fixture-${index}`, time, intervalSeconds: step, cpuPct: 34, memory: { usedPct: 40 },
    durationMs: 3, volumes: [{ drive: 'C:', freePct: 2, freeBytes: 100, totalBytes: 5000 }],
    deepTime: new Date(base + (Math.floor(index / 4) * 4 * step + gap) * 1000).toISOString(),
    battery: { present: false }, disks: [], logs: [], processes: [], coverage: { processes: 'ok: fixture' } };
}
function seed(db, from, until, step = 30, gap = 0) {
  const insert = db.prepare('INSERT INTO samples VALUES(?,?,?)');
  db.exec('BEGIN');
  for (let index = from; index < until; index++) {
    const item = sample(index, step, gap);
    insert.run(item.id, item.time, JSON.stringify(item));
  }
  db.exec('COMMIT');
}
function opening(replay, index, transition = 'OPEN', first = index) {
  const samples = replay.request.telemetry.records;
  const row = samples[index];
  const start = samples[first];
  const id = hash(replay.request.replay_id, 'process', start.sample_id);
  return { finding_id: id, event_id: hash(id, row.sample_id, transition), subsystem: 'process',
    finding_type: 'DETECTION', transition, state: transition === 'CLOSE' ? 'closed' : 'active', severity: 'advisory',
    first_at: start.sample_time, decision_at: row.sample_time, latest_evidence_at: row.sample_time,
    evidence: { quality: 'fresh', arbitrary: 'private-content' }, reason: transition === 'CLOSE' ? 'fresh_recovery' : null };
}
function request(query = '', headers = {}, method = 'GET') {
  return new Request('http://127.0.0.1:3000/api/monitor/shadow?' + query, { method,
    headers: { host: '127.0.0.1:3000', 'sec-fetch-site': 'same-origin', 'x-ultron-shadow-read': '1', ...headers } });
}
async function get(root, query, configured = true) {
  const response = handleShadow(request(query), root, configured);
  assert.equal(response.status, 200);
  return response.json();
}

test('read-only loopback contract, query bounds, privacy and v0/v1/v2 migration', async context => {
  const { root, db } = fixture(context);
  assert.equal((await get(root, '', false)).state, 'DISABLED');
  assert.equal((await get(root, '')).state, 'INSUFFICIENT_HISTORY');
  assert.equal(handleShadow(request('', { 'sec-fetch-site': 'cross-site' }), root).status, 403);
  assert.equal(handleShadow(request('', { origin: 'http://example.test' }), root).status, 403);
  assert.equal(handleShadow(request('', { host: 'example.test' }), root).status, 403);
  assert.equal(handleShadow(request('', { 'x-ultron-shadow-read': '0' }), root).status, 403);
  assert.equal(handleShadow(request('', {}, 'POST'), root).status, 405);
  for (const query of ['view=findings&limit=51', 'view=findings&limit=01', 'view=metrics&offset=2',
    'view=findings&offset=1001', 'view=findings&offset=0&offset=1', 'view=transitions&finding=SQL', 'sql=select']) {
    assert.equal(handleShadow(request(query), root).status, 400);
  }
  migrate(db);
  db.exec('CREATE TABLE shadow_eligibility (collision INTEGER)');
  assert.throws(() => migrateObservability(db));
  assert.equal(db.prepare('PRAGMA user_version').get().user_version, 1);
  assert.equal(db.prepare("SELECT count(*) AS n FROM sqlite_master WHERE name='shadow_runs'").get().n, 0);
  db.exec('DROP TABLE shadow_eligibility');
  assert.equal((await get(root, 'view=metrics')).eligibility, null);
  migrateObservability(db);
  migrateObservability(db);
  seed(db, 0, 182);
  const saved = checkpoint(db);
  const replay = exportReplay(db, 'fixture-181', saved.installation, saved);
  const first = opening(replay, 1);
  consume(db, replay, [first, opening(replay, 2, 'CLOSE', 1)], saved);
  const result = await get(root, 'view=findings&limit=1');
  assert.equal(result.items[0].opening, 'HISTORICAL_BOOTSTRAP');
  assert.equal(result.items[0].state, 'closed');
  assert.ok(!JSON.stringify(result).includes('private-content'));
  assert.equal(result.next, null);
  const history = await get(root, `view=transitions&limit=1&finding=${first.finding_id}`);
  assert.equal(history.items[0].transition, 'OPEN');
  assert.equal(history.next, 1);
  assert.equal((await get(root, `view=transitions&offset=1&finding=${first.finding_id}`)).items[0].transition, 'CLOSE');
  assert.equal((await get(root, 'view=metrics')).eligibility.eligible, 0);
  assert.equal((await get(root, 'view=status')).checkpoint.anchorExists, true);
  const durations = [];
  for (let index = 0; index < 30; index++) {
    const start = performance.now();
    await get(root, 'view=metrics');
    durations.push(performance.now() - start);
  }
  report('readOnlyHandler', durations);
});

test('three simulated days of overlap, rotation and restart keep simulated ownership durable', context => {
  const { db, file } = fixture(context);
  const stride = config.replay_stride_rows;
  const final = config.simulated_days * 86400 / config.nominal_seconds;
  seed(db, 0, final + 1);
  migrateObservability(db);
  let connection = db, prior = [], rotated = 0, repeated = 0, eligible = 0, raw = 0, persisted = 0;
  const windows = [], restarts = [], interruptions = [];
  let episodes = 0;
  try {
    for (let index = 180; index <= final; index += stride) {
      if (index > 180 && (index - 180) % (stride * 8) === 0) {
        if (connection !== db) connection.close();
        connection = new DatabaseSync(file);
        restarts.push({ trigger: index, checkpointRow: (checkpoint(connection).asof - base) / 30000 });
      }
      const saved = checkpoint(connection);
      const replay = exportReplay(connection, `fixture-${index}`, saved.installation, saved);
      const rebased = saved.replay !== replay.request.replay_id;
      if (rebased) prior = [];
      if (index > 180 && !rebased && index % (stride * 3) === 60) {
        const end = replay.request.telemetry.records.length - 1;
        prior.push(opening(replay, end - 1), opening(replay, end, 'CLOSE', end - 1));
        episodes++;
      }
      const result = consume(connection, replay, prior, saved);
      const replayed = consume(connection, replay, prior, checkpoint(connection));
      assert.equal(replayed.openings, 0);
      assert.equal(replayed.hypotheticalInterruptions, 0);
      assert.equal(replayed.repeated, prior.length);
      if (result.rotated) assert.equal(result.hypotheticalInterruptions, 0);
      raw += result.rawTransitions + replayed.rawTransitions;
      repeated += result.repeated + replayed.repeated;
      persisted += result.savedTransitions;
      eligible += result.hypotheticalInterruptions;
      rotated += Number(result.rotated);
      windows.push({ trigger: index, rows: replay.request.telemetry.records.length, rebased: result.rotated, openings: result.openings });
      interruptions.push({ trigger: index, eligible: result.hypotheticalInterruptions, repeated: replayed.repeated });
    }
    assert.ok(rotated >= 5 && restarts.length > 3);
    assert.equal(eligible, episodes);
    assert.equal(connection.prepare("SELECT count(*) AS n FROM shadow_eligibility WHERE reason='ELIGIBLE'").get().n, eligible);
    report('replay', { days: (windows.at(-1).trigger - 180) * config.nominal_seconds / 86400,
      fixtureDays: config.simulated_days, raw, validated: null, persisted, repeated, rotated, eligible, episodes,
      genuineRecurrences: episodes - 1, duplicateLogicalOpenings: 0, duplicateHypotheticalOpenings: 0, windows, restarts, interruptions,
      note: 'Direct store lifecycle stress; validated count applies only to real HTTP replay.' });
  } finally { if (connection !== db) connection.close(); }
});

test('row limit, retention, sleep gap and uncertain rebase do not grant duplicate eligibility', context => {
  const { db } = fixture(context);
  migrateObservability(db);
  seed(db, 0, 1900, 15);
  let saved = checkpoint(db);
  let replay = exportReplay(db, 'fixture-360', saved.installation, saved);
  const first = opening(replay, 360);
  consume(db, replay, [first], saved);
  saved = checkpoint(db);
  replay = exportReplay(db, 'fixture-1799', saved.installation, saved);
  assert.equal(replay.request.telemetry.records.length, 1800);
  assert.ok(Buffer.byteLength(JSON.stringify(replay.request)) <= 2097152);
  consume(db, replay, [first], saved);
  saved = checkpoint(db);
  replay = exportReplay(db, 'fixture-1800', saved.installation, saved);
  assert.notEqual(replay.request.replay_id, saved.replay);
  consume(db, replay, [], saved);
  assert.equal(db.prepare('SELECT state FROM shadow_findings WHERE id=?').get(first.finding_id).state, 'superseded');
  saved = checkpoint(db);
  replay = exportReplay(db, 'fixture-1810', saved.installation, saved);
  const late = opening(replay, 366);
  assert.equal(consume(db, replay, [late], saved).hypotheticalInterruptions, 0);
  assert.equal(db.prepare('SELECT reason FROM shadow_eligibility WHERE finding=?').get(late.finding_id).reason, 'AMBIGUOUS_REBASE');
  saved = checkpoint(db);
  replay = exportReplay(db, 'fixture-1820', saved.installation, saved);
  const recovered = opening(replay, 371, 'CLOSE', 366);
  const recurrence = opening(replay, 374);
  assert.equal(consume(db, replay, [late, recovered, recurrence], saved).hypotheticalInterruptions, 1);
  db.prepare('DELETE FROM samples WHERE time<?').run(sample(1850, 15).time);
  saved = checkpoint(db);
  assert.throws(() => exportReplay(db, 'fixture-1899', saved.installation, saved), { code: 'INSUFFICIENT_HISTORY' });
  assert.deepEqual(checkpoint(db), saved);
  seed(db, 1900, 2261, 15, 900);
  assert.throws(() => exportReplay(db, 'fixture-1901', saved.installation, saved), { code: 'INSUFFICIENT_HISTORY' });
  replay = exportReplay(db, 'fixture-2260', saved.installation, saved);
  assert.equal(replay.startId, 'fixture-1900');
  assert.equal(consume(db, replay, [], saved).hypotheticalInterruptions, 0);
});

test('equivalent deterministic workloads survive overlapping shadow writes and bounded lock rejection', async context => {
  const disabled = fixture(context), enabled = fixture(context);
  const timings = [];
  for (const [label, target] of [['disabled', disabled], ['shadow', enabled]]) {
    const { db, store, file } = target;
    if (label === 'shadow') migrateObservability(db);
    seed(db, 0, 181);
    const shadowDb = new DatabaseSync(file);
    shadowDb.exec('PRAGMA busy_timeout=10');
    let critical = 0, dropped = 0, busy = 0;
    try {
      for (let index = 181; index < 221; index++) {
        const item = sample(index);
        const started = performance.now();
        item.analysis = analyze(item, []);
        try { store.saveSample(item); } catch { dropped++; }
        if (store.getIssues().some(issue => issue.severity === 'critical')) critical++;
        timings.push({ mode: label, stage: 'deterministic', ms: performance.now() - started });
        if (label !== 'shadow') continue;
        const shadowStarted = performance.now();
        const saved = checkpoint(shadowDb);
        const replay = exportReplay(shadowDb, item.id, saved.installation, saved);
        if (index % 5 === 0) {
          db.exec('BEGIN IMMEDIATE');
          try { consume(shadowDb, replay, [], saved); }
          catch (error) { if ((error.errcode & 255) === 5) busy++; else throw error; }
          finally { db.exec('ROLLBACK'); }
        } else {
          consume(shadowDb, replay, [], saved);
          timings.push({ mode: label, stage: 'shadow_export_and_write', ms: performance.now() - shadowStarted });
        }
      }
    } finally { shadowDb.close(); }
    assert.equal(dropped, 0);
    assert.equal(critical, 40);
    if (label === 'shadow') assert.equal(busy, 8);
    report('isolation', { mode: label, samples: 40, dropped, critical, busy });
  }
  const p95 = mode => {
    const values = timings.filter(row => row.mode === mode && row.stage === 'deterministic').map(row => row.ms).sort((a,b) => a-b);
    return values[Math.ceil(values.length * .95) - 1];
  };
  const delta = p95('shadow') - p95('disabled');
  report('latency', { delta, threshold: config.deterministic_p95_max_added_ms, timings });
  assert.ok(delta <= config.deterministic_p95_max_added_ms);
});

test('real authenticated inference replays three days through durable rebases and read-only API', {
  skip: !process.env.ULTRON_PHASE7_FIXTURE_PORT, timeout: 120000
}, async context => {
  const { db, root, file } = fixture(context);
  const insert = db.prepare('INSERT INTO samples VALUES(?,?,?)');
  db.exec('BEGIN');
  for (let index = 0; index <= 8640; index++) {
    const item = sample(index);
    item.volumes[0].freePct = 55;
    const phase = index % 1440;
    const count = phase >= 300 && phase < 321 ? 2 : phase >= 321 && phase < 360 ? 5 : 0;
    item.processes = Array.from({ length: count }, () => ({ responding: false }));
    insert.run(item.id, item.time, JSON.stringify(item));
  }
  db.exec('COMMIT');
  const config = { port: Number(process.env.ULTRON_PHASE7_FIXTURE_PORT), token: process.env.ULTRON_INFERENCE_TOKEN };
  const results = [];
  for (let index = 180; index <= 8640; index += 360) {
    const connection = new DatabaseSync(file);
    try {
      const run = createJob(connection, config);
      const result = await run(`fixture-${index}`);
      assert.ok(result.ok, JSON.stringify({ index, result }));
      results.push({ trigger: index, ...result });
      if (index % 1440 === 180) {
        const repeated = await run(`fixture-${index}`);
        assert.equal(repeated.openings, 0);
        assert.equal(repeated.hypotheticalInterruptions, 0);
        results.push({ trigger: index, repeat: true, ...repeated });
      }
    } finally { connection.close(); }
  }
  const metrics = await get(root, 'view=metrics');
  const findings = await get(root, 'view=findings');
  assert.ok(metrics.rebases > 0 && metrics.repeated > 0);
  assert.ok(findings.items.length > 0);
  assert.equal(metrics.duplicateRejected, 0);
  assert.equal(metrics.eligibility.eligible, results.reduce((count, row) => count + row.hypotheticalInterruptions, 0));
  assert.ok(results.filter(row => row.rotated).every(row => row.hypotheticalInterruptions === 0));
  report('realReplay', { results, metrics: { raw: metrics.raw, validated: metrics.validated,
    repeated: metrics.repeated, persisted: metrics.persisted, rebases: metrics.rebases,
    duplicateRejected: metrics.duplicateRejected, eligibility: metrics.eligibility } });
});
