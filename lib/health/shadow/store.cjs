const { randomUUID } = require('node:crypto');
const { ShadowError, POLICY, utcMillis, hash } = require('./contract.cjs');

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
    if (version() === 1) return;
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
  db.prepare('INSERT OR REPLACE INTO shadow_status VALUES (1,?,?,?)').run(state, code, now);
  db.prepare('INSERT INTO shadow_attempts(at,state,code) VALUES (?,?,?)').run(now, state, code);
  db.exec('DELETE FROM shadow_attempts WHERE id <= (SELECT MAX(id)-500 FROM shadow_attempts)');
}

const checkpoint = db => db.prepare('SELECT * FROM shadow_checkpoint WHERE id=1').get();

function consume(db, replay, findings, expected) {
  return transaction(db, () => {
    const current = checkpoint(db);
    if (!current || current.policy !== POLICY || current.replay !== expected.replay ||
        current.asof !== expected.asof || current.cursor !== expected.cursor || current.installation !== expected.installation ||
        (current.asof !== null && replay.end < current.asof)) throw new ShadowError('STALE_RESPONSE');
    if (!db.prepare('SELECT 1 FROM samples WHERE id=?').get(replay.startId) ||
        !db.prepare('SELECT 1 FROM samples WHERE id=?').get(replay.request.telemetry.records.at(-1).sample_id)) {
      throw new ShadowError('STALE_RESPONSE');
    }
    const namespace = replay.request.replay_id;
    const rotated = current.replay !== null && current.replay !== namespace;
    const cutoff = rotated ? replay.end : current.asof;
    const now = Date.now();
    if (rotated) {
      db.prepare("UPDATE shadow_findings SET state='superseded',updated_at=? WHERE replay=? AND state='active'").run(now, current.replay);
      db.prepare('DELETE FROM shadow_consumed WHERE replay=?').run(current.replay);
    }
    let cursor = rotated ? null : current.cursor;
    let openings = 0, newEvents = 0, repeated = 0;
    const expectedRepeats = rotated ? 0 : db.prepare('SELECT count(*) AS count FROM shadow_consumed WHERE replay=?')
      .get(namespace).count;
    const getConsumed = db.prepare('SELECT digest FROM shadow_consumed WHERE id=?');
    const saveConsumed = db.prepare('INSERT INTO shadow_consumed VALUES (?,?,?,?)');
    const getFinding = db.prepare('SELECT * FROM shadow_findings WHERE id=?');
    const saveTransition = db.prepare('INSERT INTO shadow_transitions VALUES (?,?,?,?,?)');
    const saveFinding = db.prepare(`INSERT INTO shadow_findings VALUES (?,?,?,?,?,?,?,?,?,?)
      ON CONFLICT(id) DO UPDATE SET state=excluded.state,last_at=excluded.last_at,
      updated_at=excluded.updated_at,data=excluded.data`);
    for (const finding of findings) {
      const time = utcMillis(finding.decision_at);
      const serialized = JSON.stringify(finding);
      const digest = hash(JSON.stringify(Object.entries(finding).sort().map(([key, value]) =>
        [key, key === 'evidence' ? Object.entries(value).sort() : value])));
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
          if (!rotated) openings++;
        } else if (!existing && cutoff !== null) {
          continue;
        } else if (!existing || existing.state !== 'active') throw new ShadowError('CONFLICTING_EVENT');
        saveFinding.run(finding.finding_id, namespace, finding.subsystem, finding.state, time,
          existing?.created_at ?? now, now, 'python-shadow', POLICY, serialized);
      }
      saveTransition.run(finding.event_id, namespace, finding.finding_id, time, serialized);
      if (!rotated) newEvents++;
    }
    if (repeated !== expectedRepeats) throw new ShadowError('CONFLICTING_HISTORY');
    db.prepare('UPDATE shadow_checkpoint SET start_id=?,replay=?,asof=?,cursor=? WHERE id=1')
      .run(replay.startId, namespace, replay.end, cursor);
    const old = now - 7 * 86400000;
    db.prepare('DELETE FROM shadow_transitions WHERE replay<>? AND at<?').run(namespace, old);
    db.prepare('DELETE FROM shadow_findings WHERE replay<>? AND updated_at<?').run(namespace, old);
    status(db, 'SHADOW', rotated ? 'REPLAY_REBASED' : 'OK');
    return { openings, newEvents, rotated, rawTransitions: findings.length,
      hypotheticalInterruptions: current.asof === null ? 0 : openings };
  });
}

function clearShadow(db) {
  if (!db.prepare("SELECT 1 FROM sqlite_master WHERE type='table' AND name='shadow_checkpoint'").get()) return;
  db.exec('DELETE FROM shadow_findings; DELETE FROM shadow_transitions; DELETE FROM shadow_consumed; DELETE FROM shadow_status; DELETE FROM shadow_attempts;');
  db.prepare('UPDATE shadow_checkpoint SET installation=?,start_id=NULL,replay=NULL,asof=NULL,cursor=NULL WHERE id=1')
    .run(randomUUID());
}

module.exports = { migrate, checkpoint, consume, status, clearShadow };
