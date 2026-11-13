const { performance } = require('node:perf_hooks');
const { createClient } = require('./client.cjs');
const { exportReplay } = require('./export.cjs');
const { verifyResponse, ShadowError } = require('./contract.cjs');
const { checkpoint, consume, migrate, status } = require('./store.cjs');

const NON_RETRY = new Set(['INVALID_CONFIG', 'INCOMPATIBLE_DB']);
const REJECTED = new Set(['INVALID_TELEMETRY', 'STALE_TRIGGER', 'STALE_RESPONSE', 'CONTRACT_REJECTED', 'INVALID_RESPONSE',
  'CONFLICTING_HISTORY', 'CONFLICTING_EVENT', 'DUPLICATE_OPEN', 'INSUFFICIENT_HISTORY', 'INCOMPATIBLE', 'AUTH_REJECTED']);

function createJob(db, config) {
  const client = createClient(config);
  return async sampleId => {
    const timing = {};
    try {
      migrate(db);
      const saved = checkpoint(db);
      const started = performance.now();
      const replay = exportReplay(db, sampleId, saved.installation, saved);
      timing.exportMs = performance.now() - started;
      const body = JSON.stringify(replay.request);
      timing.encodeMs = performance.now() - started - timing.exportMs;
      const sent = performance.now();
      const output = await client.infer(body);
      timing.httpMs = performance.now() - sent;
      const validating = performance.now();
      const findings = verifyResponse(output, replay.request);
      timing.validateMs = performance.now() - validating;
      const persisting = performance.now();
      const counts = consume(db, replay, findings, saved);
      timing.persistMs = performance.now() - persisting;
      return { ok: true, ...counts, timing, rows: replay.request.telemetry.records.length };
    } catch (error) {
      const code = error instanceof ShadowError ? error.code :
        [5, 6].includes(error.errcode & 255) ? 'SQLITE_BUSY' : 'INTERNAL_ERROR';
      const state = code === 'INCOMPATIBLE' || NON_RETRY.has(code) ? 'INCOMPATIBLE' :
        ['UNAVAILABLE', 'TIMEOUT', 'AUTH_REJECTED'].includes(code) ? 'UNAVAILABLE' : 'DEGRADED';
      try { status(db, state, code); } catch {}
      return { ok: false, code, transient: !REJECTED.has(code) && !NON_RETRY.has(code) };
    }
  };
}

module.exports = { createJob };
