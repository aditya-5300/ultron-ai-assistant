const { randomUUID } = require('node:crypto');
const { ShadowError, POLICY, utcMillis, hash } = require('./contract.cjs');
const { openingEligibility, recordOpening } = require('./eligibility.cjs');
const preparedStatements = new WeakMap();

function statement(db, sql) {
  let cache = preparedStatements.get(db);
  if (!cache) preparedStatements.set(db, cache = new Map());
  if (!cache.has(sql)) cache.set(sql, db.prepare(sql));
  return cache.get(sql);
}

function transaction(db, fn) {
  db.exec('BEGIN IMMEDIATE');
  try {
    const result = fn();
    db.exec('COMMIT');
    return result;
  } catch (error) {
    db.exec('ROLLBACK');
    throw error;
  }
}

function migrate(db) {
  const version = () => db.prepare('PRAGMA user_version').get().user_version;
  if (version() === 1) return;
  transaction(db, () => {
    if ([1, 2].includes(version())) return;
    if (version() !== 0) throw new ShadowError('INCOMPATIBLE_DB');
    db.exec(`
      CREATE TABLE shadow_status (id INTEGER PRIMARY KEY CHECK(id=1), state TEXT NOT NULL, code TEXT NOT NULL, at INTEGER NOT NULL);
      CREATE TABLE shadow_attempts (id INTEGER PRIMARY KEY, at INTEGER NOT NULL, state TEXT NOT NULL, code TEXT NOT NULL);
      CREATE TABLE shadow_checkpoint (id INTEGER PRIMARY KEY CHECK(id=1), installation TEXT NOT NULL,
        start_id TEXT, replay TEXT, asof REAL, cursor TEXT, policy TEXT NOT NULL);
      CREATE TABLE shadow_findings (id TEXT PRIMARY KEY, replay TEXT NOT NULL, subsystem TEXT NOT NULL,
        state TEXT NOT NULL, last_at REAL NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
        origin TEXT NOT NULL, policy TEXT NOT NULL, data TEXT NOT NULL);
      CREATE INDEX shadow_findings_replay ON shadow_findings(replay,state);
      CREATE TABLE shadow_consumed (id TEXT PRIMARY KEY, replay TEXT NOT NULL, at REAL NOT NULL, digest TEXT NOT NULL);
      CREATE INDEX shadow_consumed_replay ON shadow_consumed(replay,at);
      CREATE TABLE shadow_transitions (id TEXT PRIMARY KEY, replay TEXT NOT NULL, finding TEXT NOT NULL, at REAL NOT NULL, data TEXT NOT NULL);
      CREATE INDEX shadow_transitions_replay ON shadow_transitions(replay,at);
      PRAGMA user_version=1;
    `);
    db.prepare('INSERT INTO shadow_checkpoint VALUES (1,?,NULL,NULL,NULL,NULL,?)').run(randomUUID(), POLICY);
  });
}

function status(db, state, code) {
  const now = Date.now();
  statement(db, 'INSERT OR REPLACE INTO shadow_status VALUES (1,?,?,?)').run(state, code, now);
  statement(db, 'INSERT INTO shadow_attempts(at,state,code) VALUES (?,?,?)').run(now, state, code);
  statement(db, 'DELETE FROM shadow_attempts WHERE id <= (SELECT MAX(id)-500 FROM shadow_attempts)').run();
}

function migrateObservability(db) {
  const version = () => db.prepare('PRAGMA user_version').get().user_version;
  if (version() === 2) return;
  migrate(db);
  transaction(db, () => {
    if (version() === 2) return;
    if (version() !== 1) throw new ShadowError('INCOMPATIBLE_DB');
    db.exec(`
      CREATE TABLE shadow_runs (id INTEGER PRIMARY KEY, at INTEGER NOT NULL, state TEXT NOT NULL,
        code TEXT NOT NULL, duration REAL NOT NULL, metrics TEXT NOT NULL);
      CREATE TABLE shadow_eligibility (finding TEXT PRIMARY KEY, event TEXT UNIQUE NOT NULL, replay TEXT NOT NULL,
        at REAL NOT NULL, policy TEXT NOT NULL, reason TEXT NOT NULL);
      CREATE INDEX shadow_eligibility_time ON shadow_eligibility(at);
      CREATE INDEX shadow_transitions_finding ON shadow_transitions(finding,at,id);
      CREATE INDEX shadow_transitions_recent ON shadow_transitions(at,id);
      CREATE INDEX shadow_findings_recent ON shadow_findings(last_at,id);
      CREATE TABLE shadow_rebase_guard (subsystem TEXT PRIMARY KEY, last_at REAL NOT NULL);
      PRAGMA user_version=2;
    `);
  });
}

function recordRun(db, state, code, duration, metrics) {
  statement(db, 'INSERT INTO shadow_runs(at,state,code,duration,metrics) VALUES (?,?,?,?,?)')
    .run(Date.now(), state, code, duration, JSON.stringify(metrics));
  statement(db, 'DELETE FROM shadow_runs WHERE id <= (SELECT MAX(id)-500 FROM shadow_runs)').run();
}

const checkpoint = db => statement(db, 'SELECT * FROM shadow_checkpoint WHERE id=1').get();

function consume(db, replay, findings, expected, finish) {
  const prepared = findings.map(finding => ({
    finding, time: utcMillis(finding.decision_at), serialized: JSON.stringify(finding),
    digest: hash(JSON.stringify(Object.entries(finding).sort().map(([key, value]) =>
      [key, key === 'evidence' ? Object.entries(value).sort() : value])))
  }));
  const sampleExists = statement(db, 'SELECT 1 FROM samples WHERE id=?');
  const version = statement(db, 'PRAGMA user_version');
  const countConsumed = statement(db, 'SELECT count(*) AS count FROM shadow_consumed WHERE replay=?');
  const getConsumed = statement(db, 'SELECT digest FROM shadow_consumed WHERE id=?');
  const saveConsumed = statement(db, 'INSERT INTO shadow_consumed VALUES (?,?,?,?)');
  const getFinding = statement(db, 'SELECT * FROM shadow_findings WHERE id=?');
  const saveTransition = statement(db, 'INSERT INTO shadow_transitions VALUES (?,?,?,?,?)');
  const saveFinding = statement(db, `INSERT INTO shadow_findings VALUES (?,?,?,?,?,?,?,?,?,?)
    ON CONFLICT(id) DO UPDATE SET state=excluded.state,last_at=excluded.last_at,
    updated_at=excluded.updated_at,data=excluded.data`);
  const updateCheckpoint = statement(db, 'UPDATE shadow_checkpoint SET start_id=?,replay=?,asof=?,cursor=? WHERE id=1');
  return transaction(db, () => {
    const current = checkpoint(db);
    if (!current || current.policy !== POLICY || current.replay !== expected.replay ||
        current.asof !== expected.asof || current.cursor !== expected.cursor || current.installation !== expected.installation ||
        (current.asof !== null && replay.end < current.asof)) throw new ShadowError('STALE_RESPONSE');
    if (!sampleExists.get(replay.startId) || !sampleExists.get(replay.request.telemetry.records.at(-1).sample_id)) {
      throw new ShadowError('STALE_RESPONSE');
    }
    const namespace = replay.request.replay_id;
    const rotated = current.replay !== null && current.replay !== namespace;
    const cutoff = rotated ? replay.end : current.asof;
    const now = Date.now();
    const observability = version.get().user_version === 2;
    if (rotated) {
      if (observability) {
        statement(db, `INSERT INTO shadow_rebase_guard SELECT subsystem,last_at FROM shadow_findings WHERE replay=? AND state='active'
          ON CONFLICT(subsystem) DO UPDATE SET last_at=max(shadow_rebase_guard.last_at,excluded.last_at)`)
          .run(current.replay);
      }
      statement(db, "UPDATE shadow_findings SET state='superseded',updated_at=? WHERE replay=? AND state='active'").run(now, current.replay);
      statement(db, 'DELETE FROM shadow_consumed WHERE replay=?').run(current.replay);
    }
    let cursor = rotated ? null : current.cursor;
    let openings = 0, newEvents = 0, repeated = 0, eligible = 0, savedTransitions = 0;
    const expectedRepeats = rotated ? 0 : countConsumed.get(namespace).count;
    for (const { finding, time, serialized, digest } of prepared) {
      const prior = getConsumed.get(finding.event_id);
      if (prior) {
        if (prior.digest !== digest) throw new ShadowError('CONFLICTING_EVENT');
        repeated++;
        continue;
      }
      if (!rotated && cutoff !== null && time <= cutoff) throw new ShadowError('CONFLICTING_HISTORY');
      saveConsumed.run(finding.event_id, namespace, time, digest);
      cursor = finding.event_id;
      const existing = getFinding.get(finding.finding_id);
      if (finding.transition !== 'INSUFFICIENT_EVIDENCE') {
        if (finding.transition === 'OPEN') {
          if (existing) throw new ShadowError('DUPLICATE_OPEN');
          if (observability) {
            const ambiguous = statement(db, 'SELECT 1 FROM shadow_rebase_guard WHERE subsystem=?').get(finding.subsystem);
            eligible += recordOpening(db, finding, namespace, time,
              openingEligibility(finding.transition, current.asof, rotated, time, Boolean(ambiguous)));
          }
          if (!rotated) openings++;
        } else if (!existing && cutoff !== null) {
          continue;
        } else if (!existing || existing.state !== 'active') throw new ShadowError('CONFLICTING_EVENT');
        saveFinding.run(finding.finding_id, namespace, finding.subsystem, finding.state, time,
          existing?.created_at ?? now, now, 'python-shadow', POLICY, serialized);
        if (observability && finding.transition === 'CLOSE' && finding.reason === 'fresh_recovery' &&
            finding.evidence?.quality === 'fresh') {
          statement(db, 'DELETE FROM shadow_rebase_guard WHERE subsystem=? AND last_at<?').run(finding.subsystem, time);
        }
      }
      saveTransition.run(finding.event_id, namespace, finding.finding_id, time, serialized);
      savedTransitions++;
      if (!rotated) newEvents++;
    }
    if (repeated !== expectedRepeats) throw new ShadowError('CONFLICTING_HISTORY');
    if (current.start_id !== replay.startId || current.replay !== namespace || current.asof !== replay.end || current.cursor !== cursor) {
      updateCheckpoint.run(replay.startId, namespace, replay.end, cursor);
    }
    const old = now - 7 * 86400000;
    statement(db, 'DELETE FROM shadow_transitions WHERE replay<>? AND at<?').run(namespace, old);
    statement(db, 'DELETE FROM shadow_findings WHERE replay<>? AND updated_at<?').run(namespace, old);
    status(db, 'SHADOW', rotated ? 'REPLAY_REBASED' : 'OK');
    if (observability) statement(db, 'DELETE FROM shadow_eligibility WHERE replay<>? AND at<?').run(namespace, old);
    const counts = { openings, newEvents, rotated, rawTransitions: findings.length, repeated, savedTransitions,
      hypotheticalInterruptions: observability ? eligible : current.asof === null ? 0 : openings };
    finish?.(counts);
    return counts;
  });
}

function clearShadow(db) {
  if (!db.prepare("SELECT 1 FROM sqlite_master WHERE type='table' AND name='shadow_checkpoint'").get()) return;
  db.exec('DELETE FROM shadow_findings; DELETE FROM shadow_transitions; DELETE FROM shadow_consumed; DELETE FROM shadow_status; DELETE FROM shadow_attempts;');
  if (db.prepare('PRAGMA user_version').get().user_version === 2) db.exec('DELETE FROM shadow_runs; DELETE FROM shadow_eligibility; DELETE FROM shadow_rebase_guard;');
  db.prepare('UPDATE shadow_checkpoint SET installation=?,start_id=NULL,replay=NULL,asof=NULL,cursor=NULL WHERE id=1')
    .run(randomUUID());
}

module.exports = { migrate, migrateObservability, recordRun, checkpoint, consume, status, clearShadow };
