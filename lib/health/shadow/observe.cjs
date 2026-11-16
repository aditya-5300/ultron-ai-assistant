const fs = require('node:fs');
const path = require('node:path');
const { DatabaseSync } = process.getBuiltinModule('node:sqlite');
const { utcMillis } = require('./contract.cjs');
const { NOTIFICATION_POLICY } = require('./eligibility.cjs');

const hex = /^[a-f0-9]{64}$/;
const statuses = ['SHADOW', 'UNAVAILABLE', 'INCOMPATIBLE', 'DEGRADED'];
const codes = 'OK REPLAY_REBASED INVALID_CONFIG INCOMPATIBLE_DB INVALID_TELEMETRY STALE_TRIGGER STALE_RESPONSE CONTRACT_REJECTED INVALID_RESPONSE CONFLICTING_HISTORY CONFLICTING_EVENT DUPLICATE_OPEN INSUFFICIENT_HISTORY INCOMPATIBLE AUTH_REJECTED UNAVAILABLE TIMEOUT SQLITE_BUSY INTERNAL_ERROR REDIRECT_REJECTED WRITER_DEFERRED'.split(' ');
const scopes = ['cpu', 'disk_capacity', 'process'];
const pick = (value, values) => values.includes(value) ? value : null;
const number = value => Number.isFinite(value) && value >= 0 ? value : null;
const time = value => number(utcMillis(value));
const parse = value => { try { return JSON.parse(value) || {}; } catch { return {}; } };
const cleanRun = run => run ? { at: number(run.at), state: pick(run.state, statuses), code: pick(run.code, codes) } : null;

function finding(row) {
  const data = parse(row.data);
  return { id: hex.test(row.finding || row.id) ? row.finding || row.id : null,
    subsystem: pick(data.subsystem, scopes), type: pick(data.finding_type, ['FORECAST', 'DETECTION', 'INSUFFICIENT_EVIDENCE']),
    transition: pick(data.transition, ['OPEN', 'UPDATE', 'ESCALATE', 'CLOSE', 'INSUFFICIENT_EVIDENCE']),
    state: pick(row.state || data.state, ['active', 'closed', 'superseded', 'insufficient_evidence']),
    severity: pick(data.severity, ['advisory', 'early_warning', 'critical_reference', 'none']),
    firstAt: time(data.first_at), at: time(data.decision_at), evidenceAt: time(data.latest_evidence_at),
    quality: pick(data.evidence?.quality, ['fresh', 'cached', 'unavailable']),
    definition: pick(data.definition_version, ['phase45c-v1', 'phase5-targets-v1']),
    policy: 'phase6-approved-v1', horizon: data.horizon_seconds === 1800 ? 1800 : null,
    reason: pick(data.reason, ['fresh_recovery', 'missing_cpu_context', 'sensor_unavailable', 'warmup_or_insufficient_history']),
    opening: pick(row.eligibility, ['ELIGIBLE', 'HISTORICAL_BOOTSTRAP', 'HISTORICAL_REBASE', 'AMBIGUOUS_REBASE'])
  };
}

function query(url) {
  const names = new Set(['view', 'limit', 'offset', 'finding']);
  for (const name of url.searchParams.keys()) {
    if (!names.has(name) || url.searchParams.getAll(name).length !== 1) throw new Error('Invalid query');
  }
  const view = url.searchParams.get('view') ?? 'status';
  const limit = url.searchParams.get('limit') ?? '20';
  const offset = url.searchParams.get('offset') ?? '0';
  const id = url.searchParams.get('finding');
  if (!['status', 'metrics', 'findings', 'transitions'].includes(view) || !/^[1-9]\d?$/.test(limit) ||
      +limit > 50 || !/^(0|[1-9]\d{0,3})$/.test(offset) || +offset > 1000 ||
      (id !== null && (view !== 'transitions' || !hex.test(id))) ||
      (['status', 'metrics'].includes(view) && (url.searchParams.has('limit') || url.searchParams.has('offset')))) throw new Error('Invalid query');
  return { view, limit: +limit, offset: +offset, id };
}

function readShadow(root, options, configured, now = Date.now()) {
  const { view, limit, offset, id } = options;
  const empty = configured ? 'INSUFFICIENT_HISTORY' : 'DISABLED';
  const file = path.join(root, '.ultron', 'health.sqlite');
  const noData = () => ['findings', 'transitions'].includes(view) ? { items: [], next: null } :
    view === 'metrics' ? { available: false } : { state: empty, configured, compatibility: null };
  if (!fs.existsSync(file)) return noData();
  const db = new DatabaseSync(file, { readOnly: true });
  try {
    db.exec('PRAGMA query_only=ON; PRAGMA busy_timeout=20; BEGIN');
    const version = db.prepare('PRAGMA user_version').get().user_version;
    if (!version) return noData();
    if (![1, 2].includes(version)) return view === 'status' ? { state: configured ? 'INCOMPATIBLE' : 'DISABLED', configured } : noData();
    const modern = version === 2;
    if (view === 'findings' || view === 'transitions') {
      const paging = [limit + 1, offset];
      const rows = view === 'findings' ? db.prepare(`SELECT f.*,${modern ? 'e.reason' : 'NULL'} AS eligibility FROM shadow_findings f
        ${modern ? 'LEFT JOIN shadow_eligibility e ON e.finding=f.id' : ''} ORDER BY f.last_at DESC,f.id DESC LIMIT ? OFFSET ?`).all(...paging) :
        id ? db.prepare('SELECT * FROM shadow_transitions WHERE finding=? ORDER BY at ASC,id ASC LIMIT ? OFFSET ?').all(id, ...paging) :
          db.prepare('SELECT * FROM shadow_transitions ORDER BY at DESC,id DESC LIMIT ? OFFSET ?').all(...paging);
      const items = rows.slice(0, limit).map(finding);
      return { items, next: rows.length > limit && offset + limit <= 1000 ? offset + limit : null };
    }
    const stored = cleanRun(db.prepare('SELECT * FROM shadow_status WHERE id=1').get());
    const runs = modern ? db.prepare('SELECT * FROM shadow_runs ORDER BY id DESC LIMIT 500').all() : [];
    const recent = runs.map(row => ({ ...row, values: parse(row.metrics) }));
    const successful = recent.find(row => row.state === 'SHADOW');
    const checkpoint = db.prepare('SELECT start_id,replay,asof,cursor FROM shadow_checkpoint WHERE id=1').get();
    if (view === 'status') {
      const latest = recent[0];
      const stale = stored && now - stored.at > 2100000;
      const anchorExists = checkpoint?.start_id ? Boolean(db.prepare('SELECT 1 FROM samples WHERE id=?').get(checkpoint.start_id)) : null;
      const state = !configured ? 'DISABLED' : !stored ? empty : stale ? 'DEGRADED' :
        stored.code === 'INSUFFICIENT_HISTORY' ? 'INSUFFICIENT_HISTORY' : stored.state || 'DEGRADED';
      return { state, configured, stale: Boolean(stale), lastAttempt: stored,
        compatibility: typeof latest?.values.compatibility === 'boolean' ? latest.values.compatibility : null,
        checkpoint: checkpoint?.replay ? { namespace: hex.test(checkpoint.replay) ? checkpoint.replay : null,
          asOf: number(checkpoint.asof), ageMs: number(now - checkpoint.asof), cursorPresent: Boolean(checkpoint.cursor),
          anchorExists, start: number(successful?.values.start), rows: number(successful?.values.rows) } : null };
    }
    const attempts = db.prepare('SELECT * FROM shadow_attempts ORDER BY id DESC LIMIT 500').all();
    const counts = db.prepare('SELECT state,subsystem,count(*) AS count FROM shadow_findings GROUP BY state,subsystem').all()
      .filter(row => scopes.includes(row.subsystem) && ['active', 'closed', 'superseded'].includes(row.state));
    const durations = runs.filter(row => row.state === 'SHADOW').map(row => number(row.duration)).filter(value => value !== null).sort((a,b) => a-b);
    const percentile = fraction => durations.length ? durations[Math.ceil(durations.length * fraction) - 1] : null;
    const sum = name => {
      const measured = recent.map(row => number(row.values[name])).filter(value => value !== null);
      return measured.length ? measured.reduce((total, value) => total + value, 0) : null;
    };
    const countCode = code => attempts.filter(row => row.code === code).length;
    const scheduler = recent.find(row => row.values.scheduler)?.values.scheduler;
    return { available: true, window: 'latest 500 persisted attempts', attempts: attempts.length,
      successes: attempts.filter(row => row.state === 'SHADOW').length, failures: attempts.filter(row => row.state !== 'SHADOW').length,
      lastSuccess: cleanRun(attempts.find(row => row.state === 'SHADOW')), lastFailure: cleanRun(attempts.find(row => row.state !== 'SHADOW')),
      recentFailures: attempts.filter(row => row.state !== 'SHADOW').slice(0, 5).map(cleanRun),
      latency: modern ? { samples: durations.length, median: percentile(.5), p95: durations.length >= 20 ? percentile(.95) : null,
        latest: number(successful?.duration) } : null,
      counts, raw: modern ? sum('rawTransitions') : null, validated: modern ? sum('validated') : null,
      persisted: modern ? sum('savedTransitions') : null, repeated: modern ? sum('repeated') : null,
      staleRejected: countCode('STALE_RESPONSE') + countCode('STALE_TRIGGER'), duplicateRejected: countCode('DUPLICATE_OPEN'),
      rebases: countCode('REPLAY_REBASED'), busy: countCode('SQLITE_BUSY'), insufficient: countCode('INSUFFICIENT_HISTORY'),
      eligibility: modern ? { policy: NOTIFICATION_POLICY, eligible: db.prepare("SELECT count(*) AS count FROM shadow_eligibility WHERE reason='ELIGIBLE'").get().count,
        historical: db.prepare("SELECT count(*) AS count FROM shadow_eligibility WHERE reason IN ('HISTORICAL_BOOTSTRAP','HISTORICAL_REBASE')").get().count,
        ambiguous: db.prepare("SELECT count(*) AS count FROM shadow_eligibility WHERE reason='AMBIGUOUS_REBASE'").get().count } : null,
      scheduler: scheduler ? { accepted: number(scheduler.accepted), skipped: number(scheduler.skipped),
        completed: number(scheduler.completed), failed: number(scheduler.failed), backoffProbe: scheduler.backoffProbe === true } : null };
  } finally { db.close(); }
}

function handleShadow(request, root = process.cwd(), configured = process.env.ULTRON_INFERENCE_MODE === 'shadow') {
  const json = (body, status = 200) => Response.json(body, { status, headers: { 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' } });
  if (request.method !== 'GET') return json({ error: 'Read-only endpoint' }, 405);
  const url = new URL(request.url);
  const host = request.headers.get('host') || '';
  const origin = request.headers.get('origin');
  if (!/^(localhost|127\.0\.0\.1|\[::1\])(?::\d+)?$/.test(host) ||
      request.headers.get('sec-fetch-site') !== 'same-origin' ||
      request.headers.get('x-ultron-shadow-read') !== '1' || (origin !== null && origin !== `${url.protocol}//${host}`)) {
    return json({ error: 'Same-origin loopback application required' }, 403);
  }
  let options;
  try { options = query(url); } catch { return json({ error: 'Invalid shadow query' }, 400); }
  try { return json(readShadow(root, options, configured)); }
  catch { return json({ error: 'Shadow records temporarily unavailable' }, 503); }
}

module.exports = { handleShadow, query, readShadow };
