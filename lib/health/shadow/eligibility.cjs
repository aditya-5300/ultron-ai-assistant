const { ShadowError } = require('./contract.cjs');
const NOTIFICATION_POLICY = 'phase8-offline-v1';

function openingEligibility(transition, previousAsOf, rebased, decisionTime, ambiguous = false) {
  if (transition !== 'OPEN') return null;
  if (previousAsOf === null) return 'HISTORICAL_BOOTSTRAP';
  if (rebased) return 'HISTORICAL_REBASE';
  if (decisionTime <= previousAsOf) return 'STALE';
  if (ambiguous) return 'AMBIGUOUS_REBASE';
  return 'ELIGIBLE';
}

function recordOpening(db, finding, namespace, time, reason) {
  const result = db.prepare('INSERT OR IGNORE INTO shadow_eligibility VALUES (?,?,?,?,?,?)')
    .run(finding.finding_id, finding.event_id, namespace, time, NOTIFICATION_POLICY, reason);
  if (!result.changes) throw new ShadowError('DUPLICATE_OPEN');
  return reason === 'ELIGIBLE' ? 1 : 0;
}

module.exports = { NOTIFICATION_POLICY, openingEligibility, recordOpening };
