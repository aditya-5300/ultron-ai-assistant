const { randomUUID } = require('node:crypto');
const { VERSION, POLICY, FINGERPRINTS, SCOPE, LIMITS, ShadowError, utcMillis, hash, bounded } = require('./contract.cjs');

const ANCHOR_MS = 90 * 60 * 1000;
const MIN_HISTORY_MS = ANCHOR_MS;
const MAX_GAP_MS = 10 * 60 * 1000;
const MISSING = Object.freeze({ value: null, observation_time: null, availability: 'unavailable', quality: 'missing' });
const idPattern = /^[A-Za-z0-9_-]{1,100}$/;
const cadence = new Set([15, 30, 60, 120, 300]);
const powerPlans = new Map([
  ['381b4222-f694-41f0-9685-ff5bb260df2e', 'balanced'],
  ['8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c', 'performance'],
  ['a1841308-3541-4fab-bc81-f71556f20b4a', 'saver']
]);

function reading(value, observed, acquired, maximum) {
  if (!bounded(value, 0, maximum) || !Number.isFinite(observed) || observed > acquired ||
      acquired - observed >= 660000) return MISSING;
  return { value, observation_time: new Date(observed).toISOString(), availability: 'available',
    quality: observed === acquired ? 'fresh' : 'cached' };
}

function exportReplay(db, sampleId, installation, checkpoint, now = Date.now()) {
  const anchor = db.prepare('SELECT id,time FROM samples WHERE id=?').get(sampleId);
  const end = utcMillis(anchor?.time);
  if (!anchor || !Number.isFinite(end) || end > now + 2000) throw new ShadowError('INVALID_TELEMETRY');
  if (checkpoint && end < checkpoint.asof) throw new ShadowError('STALE_TRIGGER');
  const cutoff = new Date(end - LIMITS.max_history_seconds * 1000).toISOString();
  const history = db.prepare('SELECT id,time,data FROM samples WHERE time>=? AND time<=? ORDER BY time DESC,id DESC LIMIT ?')
    .all(cutoff, anchor.time, LIMITS.max_records + 1).reverse();
  if (history.at(-1)?.id !== sampleId) throw new ShadowError('INVALID_TELEMETRY');
  let segment = 0;
  const decoded = [];
  let previous = -Infinity;
  const ids = new Set();
  for (const row of history) {
    const time = utcMillis(row.time);
    let sample;
    try { sample = JSON.parse(row.data); } catch { throw new ShadowError('INVALID_TELEMETRY'); }
    if (!Number.isFinite(time) || new Date(time).toISOString() !== row.time || time <= previous ||
        !idPattern.test(row.id) || ids.has(row.id) || !sample || sample.time !== row.time || sample.id !== row.id ||
        !cadence.has(sample.intervalSeconds)) throw new ShadowError('INVALID_TELEMETRY');
    if (time - previous > MAX_GAP_MS) segment = decoded.length;
    decoded.push({ sample, time, id: row.id });
    ids.add(row.id);
    previous = time;
  }
  const candidates = decoded.slice(segment);
  let start = checkpoint ? candidates.findIndex(item => item.id === checkpoint.start_id) : -1;
  if (start < 0 || candidates.length - start > LIMITS.max_records ||
      end - candidates[start].time > LIMITS.max_history_seconds * 1000) {
    start = candidates.findLastIndex(item => item.time <= end - ANCHOR_MS);
  }
  if (start < 0 || end - candidates[start].time < MIN_HISTORY_MS) throw new ShadowError('INSUFFICIENT_HISTORY');
  const chosen = candidates.slice(start);
  if (chosen.length > LIMITS.max_records) throw new ShadowError('INSUFFICIENT_HISTORY');
  const startId = chosen[0].id;
  const replayId = hash(installation, startId, POLICY);
  const records = chosen.map(({ sample, time, id }, index) => {
    const volume = Array.isArray(sample.volumes) ? sample.volumes.find(item =>
      typeof item?.drive === 'string' && item.drive.toUpperCase() === 'C:') : null;
    const validProcesses = String(sample.coverage?.processes || '').startsWith('ok:') && Array.isArray(sample.processes);
    const count = validProcesses ? sample.processes.filter(item => item?.responding === false).length : null;
    const deepTime = typeof sample.deepTime === 'string'
      ? sample.deepTime.replace(/(\.\d{6})\d(?=Z$|\+00:00$)/, '$1') : null;
    const deep = utcMillis(deepTime);
    if (volume && Number.isFinite(deep) && deep > time) throw new ShadowError('INVALID_TELEMETRY');
    return { telemetry_schema_version: VERSION, source: 'ultron', source_version: 'node-shadow-v1', sample_id: id,
      sample_time: sample.time, nominal_interval_seconds: sample.intervalSeconds,
      elapsed_seconds: index ? (time - chosen[index - 1].time) / 1000 : null,
      collection_duration_seconds: bounded(sample.durationMs, 0, 120000) ? sample.durationMs / 1000 : 0,
      ac_online: typeof sample.battery?.acOnline === 'boolean' ? sample.battery.acOnline : null,
      power_plan: powerPlans.get(typeof sample.powerPlan?.id === 'string' ? sample.powerPlan.id.toLowerCase() : null) || 'unknown',
      metrics: { cpu_usage_pct: reading(sample.cpuPct, time, time, 100),
        memory_usage_pct: reading(sample.memory?.usedPct, time, time, 100),
        disk_free_pct: diskReading(volume?.freePct, deep, time, deepTime),
        selected_process_nonresponding_count: reading(count, time, time, 24) } };
  });
  const request = { api_version: VERSION, feature_schema_version: VERSION, feature_fingerprints: FINGERPRINTS,
    request_id: randomUUID(), replay_id: replayId, mode: 'bounded_replay', scope: SCOPE,
    telemetry: { telemetry_schema_version: VERSION, window_start: records[0].sample_time,
      window_end: anchor.time, as_of: anchor.time, records } };
  if (Buffer.byteLength(JSON.stringify(request)) > LIMITS.max_body_bytes) throw new ShadowError('INSUFFICIENT_HISTORY');
  return { request, startId, end };
}

function diskReading(value, observed, acquired, original) {
  const result = reading(value, observed, acquired, 100);
  return result === MISSING ? result : { ...result, observation_time: original };
}

module.exports = { exportReplay, MIN_HISTORY_MS };
