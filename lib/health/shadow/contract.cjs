const { createHash } = require('node:crypto');

const VERSION = '1.0.0';
const POLICY = 'phase6-approved-v1';
const FINGERPRINTS = Object.freeze({
  detection: 'd9c14284e9349439e07f632aaa91f077a2a8f662afb7ef9939b9666c8024ef47',
  forecast: '3a95a6eb5d0dae388f906c3ef4e14440a8d5ef0aab549a7dc34ca6f4304ed1ff'
});
const SCOPE = Object.freeze({ detection: Object.freeze(['cpu', 'disk_capacity', 'process']), forecasting: Object.freeze(['process']) });
const LIMITS = Object.freeze({ max_records: 1800, max_history_seconds: 43200, max_body_bytes: 2097152 });
const own = (object, keys) => object !== null && typeof object === 'object' && !Array.isArray(object) &&
  Object.keys(object).length === keys.length && keys.every(key => Object.hasOwn(object, key));
const hash = (...parts) => createHash('sha256').update(parts.join('|')).digest('hex');
const bounded = (value, minimum, maximum) => Number.isFinite(value) && value >= minimum && value <= maximum;
const integer = (value, minimum, maximum) => Number.isInteger(value) && bounded(value, minimum, maximum);
const utcMillis = (value) => {
  if (typeof value !== 'string' || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|\+00:00)$/.test(value)) return NaN;
  const milliseconds = Date.parse(value);
  if (!Number.isFinite(milliseconds) || new Date(milliseconds).toISOString().slice(0, 19) !== value.slice(0, 19)) return NaN;
  const fraction = /\.(\d+)/.exec(value)?.[1];
  return fraction ? Math.floor(milliseconds / 1000) * 1000 + Number('0.' + fraction) * 1000 : milliseconds;
};
const sameKeys = (actual, expected) => own(actual, Object.keys(expected)) &&
  Object.keys(expected).every(key => actual[key] === expected[key]);
const sameScope = (value, expected) => Array.isArray(value) && value.length === expected.length &&
  new Set(value).size === expected.length && value.every(key => expected.includes(key));
class ShadowError extends Error {
  constructor(code) { super(code); this.code = code; }
}
function verifyMetadata(value) {
  const fixed = { api_version: VERSION, telemetry_schema_version: VERSION, feature_schema_version: VERSION, policy_version: POLICY, ...LIMITS };
  if (!own(value, [...Object.keys(fixed), 'feature_fingerprints', 'detection_scope', 'forecasting_scope']) ||
      !Object.entries(fixed).every(([key, expected]) => value[key] === expected) ||
      !sameKeys(value.feature_fingerprints, FINGERPRINTS) ||
      !sameScope(value.detection_scope, SCOPE.detection) || !sameScope(value.forecasting_scope, SCOPE.forecasting)) {
    throw new ShadowError('INCOMPATIBLE');
  }
}

const FINDING_KEYS = ['finding_id', 'event_id', 'subsystem', 'finding_type', 'transition', 'state', 'severity',
  'first_at', 'latest_evidence_at', 'decision_at', 'definition_version', 'source', 'evidence',
  'horizon_seconds', 'minimum_useful_lead_seconds', 'target_window_end', 'reason'];
const EVIDENCE_KEYS = ['available', 'reading', 'quality', 'independent_confirmations', 'score', 'score_type'];
const HEX = /^[a-f0-9]{64}$/;
const METRIC = { cpu: 'cpu_usage_pct', disk_capacity: 'disk_free_pct', process: 'selected_process_nonresponding_count' };

function verifyResponse(value, request) {
  const bad = () => { throw new ShadowError('INVALID_RESPONSE'); };
  const fixed = { api_version: VERSION, telemetry_schema_version: VERSION, feature_schema_version: VERSION,
    policy_version: POLICY, request_id: request.request_id, replay_id: request.replay_id, advisory_only: true };
  const { records, as_of: asOf } = request.telemetry;
  if (!own(value, [...Object.keys(fixed), 'feature_fingerprints', 'as_of', 'findings', 'process_raw_qualifying_rows', 'logical_open_count']) ||
      !Object.entries(fixed).every(([key, expected]) => value[key] === expected) ||
      !sameKeys(value.feature_fingerprints, FINGERPRINTS) || utcMillis(value.as_of) !== utcMillis(asOf) ||
      !integer(value.process_raw_qualifying_rows, 0, records.length) ||
      !integer(value.logical_open_count, 0, records.length * 3) ||
      !Array.isArray(value.findings) || value.findings.length > records.length * 6) bad();
  const samples = new Map(records.map(record => [utcMillis(record.sample_time), record]));
  const seen = new Set();
  const owners = new Map();
  const active = new Map();
  let priorTime = -Infinity;
  let opens = 0;
  for (const finding of value.findings) {
    if (!own(finding, FINDING_KEYS)) bad();
    const { subsystem, finding_id: id, event_id: event, transition, finding_type: kind,
      state, severity, source, evidence, reason } = finding;
    const time = utcMillis(finding.decision_at);
    const first = utcMillis(finding.first_at);
    const sample = samples.get(time);
    if (!request.scope.detection.includes(subsystem) || typeof id !== 'string' || !HEX.test(id) ||
        typeof event !== 'string' || seen.has(event) || !sample || time < priorTime ||
        !samples.has(first) || first > time || event !== hash(id, sample.sample_id, transition) ||
        !own(evidence, EVIDENCE_KEYS) || typeof evidence.available !== 'boolean') bad();
    seen.add(event);
    priorTime = time;
    const reading = sample.metrics[METRIC[subsystem]];
    const available = reading.availability === 'available';
    if (evidence.available !== available || evidence.quality !== (available ? reading.quality : 'unavailable') ||
        evidence.reading !== reading.value || (available ? utcMillis(finding.latest_evidence_at) !== utcMillis(reading.observation_time) :
          finding.latest_evidence_at !== null) ||
        (evidence.independent_confirmations !== null && !integer(evidence.independent_confirmations, 1, records.length)) ||
        (evidence.score !== null && !bounded(evidence.score, 0, 1)) ||
        evidence.score_type !== (evidence.score === null ? null : 'uncalibrated_evidence')) bad();
    const horizon = finding.horizon_seconds;
    if (horizon === null) {
      if (finding.minimum_useful_lead_seconds !== null || finding.target_window_end !== null) bad();
    } else if (subsystem !== 'process' || horizon !== 1800 || finding.minimum_useful_lead_seconds !== 240 ||
               utcMillis(finding.target_window_end) !== first + 1800000) bad();
    if (transition === 'INSUFFICIENT_EVIDENCE') {
      if (kind !== transition || source !== 'coverage' || state !== 'insufficient_evidence' || severity !== 'none' ||
          finding.definition_version !== 'phase5-targets-v1' || horizon !== null || evidence.score !== null ||
          evidence.independent_confirmations !== null ||
          !['missing_cpu_context', 'sensor_unavailable', 'warmup_or_insufficient_history'].includes(reason) ||
          (id !== hash(request.replay_id, subsystem, 'coverage') && id !== active.get(subsystem))) bad();
      continue;
    }
    if (id !== hash(request.replay_id, subsystem, (first / 1000).toFixed(3).replace(/0+$/, '').replace(/\.$/, '.0'))) bad();
    if (kind === 'FORECAST') {
      if (!request.scope.forecasting.includes(subsystem) || subsystem !== 'process' ||
          source !== 'process_persistence' || severity !== 'early_warning' || horizon === null ||
          finding.definition_version !== 'phase5-targets-v1') bad();
    } else if (kind !== 'DETECTION' || !['advisory', 'critical_reference'].includes(severity) ||
        !['reduced_hybrid', 'process_actionable'].includes(source) || evidence.score !== null ||
        evidence.independent_confirmations !== null ||
        (source === 'process_actionable' && subsystem !== 'process') ||
        (severity === 'critical_reference' && (subsystem !== 'disk_capacity' || source !== 'reduced_hybrid')) ||
        finding.definition_version !== (source === 'reduced_hybrid' ? 'phase45c-v1' : 'phase5-targets-v1')) bad();
    const owner = owners.get(id);
    if (transition === 'OPEN') {
      if (owner || active.has(subsystem) || !available) bad();
      active.set(subsystem, id);
      opens++;
    } else if (!['UPDATE', 'ESCALATE', 'CLOSE'].includes(transition) || !owner ||
        active.get(subsystem) !== id || first !== utcMillis(owner.first_at) || horizon !== owner.horizon_seconds ||
        !available || (transition !== 'ESCALATE' && evidence.quality !== 'fresh')) bad();
    if (owner && time <= utcMillis(owner.decision_at)) bad();
    if (transition === 'CLOSE') {
      if (state !== 'closed' || reason !== 'fresh_recovery' ||
          kind !== owner.finding_type || severity !== owner.severity) bad();
      active.delete(subsystem);
    } else if (state !== 'active' || reason !== null) bad();
    if (transition === 'UPDATE' && (kind !== owner.finding_type || severity !== owner.severity)) bad();
    if (transition === 'ESCALATE' && !(kind === 'DETECTION' &&
        ((owner.finding_type === 'FORECAST' && severity === 'advisory') ||
          (owner.severity === 'advisory' && severity === 'critical_reference')))) bad();
    owners.set(id, finding);
  }
  if (opens !== value.logical_open_count) bad();
  return value.findings;
}

module.exports = { VERSION, POLICY, FINGERPRINTS, SCOPE, LIMITS, ShadowError, utcMillis, hash,
  bounded, integer, verifyMetadata, verifyResponse };
