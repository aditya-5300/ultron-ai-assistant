const assert = require('node:assert/strict');
const { test } = require('node:test');
const { DatabaseSync } = require('node:sqlite');
const { fixture, service, seed } = require('./phase8b-fixture.cjs');
const { createJob } = require('../lib/health/shadow/job.cjs');
const { migrateObservability, checkpoint } = require('../lib/health/shadow/store.cjs');

test('offline policy aliases preserve real replay identity, reject ambiguity, and reconcile after restart', {
  skip: !process.env.ULTRON_PHASE8B_PYTHON, timeout: 120000
}, async context => {
  const profiles = JSON.parse(process.env.ULTRON_PHASE9_PROFILES);
  assert.notEqual(profiles.baseline.version, profiles.candidate.version);
  assert.equal(profiles.baseline.identity, profiles.candidate.identity);
  const serviceHost = fixture(context);
  const python = await service(serviceHost);
  await python.start();
  const evaluations = [];
  for (let repeat = 0; repeat < 2; repeat++) {
    const host = fixture(context);
    seed(host.store.db, 1781, Date.UTC(2025, 1, 1), 30, true);
    migrateObservability(host.store.db);
    host.store.db.prepare('UPDATE shadow_checkpoint SET installation=?').run('phase9-fixed-fixture');
    let connection = new DatabaseSync(host.file);
    let selected = profiles.baseline;
    const output = [];
    async function sampleAt(index, action) {
      const result = await createJob(connection, python)('seed-' + index);
      assert.ok(result.ok, JSON.stringify({ action, result }));
      const { timing, ...logical } = result;
      const owners = connection.prepare('SELECT id,state,data FROM shadow_findings ORDER BY id').all()
        .map(row => ({ id: row.id, state: row.state, kind: JSON.parse(row.data).finding_type }));
      const events = connection.prepare('SELECT id,finding,data FROM shadow_transitions ORDER BY at,id').all()
        .map(row => ({ id: row.id, owner: row.finding, transition: JSON.parse(row.data).transition }));
      const consumed = connection.prepare('SELECT id,digest FROM shadow_consumed ORDER BY id').all();
      const eligible = connection.prepare('SELECT finding,event,reason FROM shadow_eligibility ORDER BY finding').all();
      const state = checkpoint(connection);
      output.push({ action, version: selected.version, index, logical, owners, events, consumed, eligible,
        replay: state.replay, cursor: state.cursor });
      return output.at(-1);
    }
    function select(profile, action) {
      if (profile.identity !== selected.identity) {
        const before = checkpoint(connection);
        output.push({ action, version: selected.version, state: 'DEGRADED_INCOMPATIBLE', checkpointUnchanged: true });
        assert.deepEqual(checkpoint(connection), before);
        return false;
      }
      selected = profile;
      return true;
    }
    try {
      await sampleAt(180, 'bootstrap');
      assert.equal((await sampleAt(180, 'same_policy_twice')).logical.openings, 0);
      const first = await sampleAt(315, 'active_forecast_before_promotion');
      assert.ok(first.owners.some(row => row.state === 'active' && row.kind === 'FORECAST'));
      assert.ok(select(profiles.candidate, 'promote'));
      assert.equal((await sampleAt(315, 'replayed_after_promotion')).logical.openings, 0);
      assert.ok(select(profiles.baseline, 'rollback_active_forecast'));
      const restoredForecast = await sampleAt(315, 'repeat_after_forecast_rollback');
      assert.equal(restoredForecast.logical.openings, 0);
      assert.equal(restoredForecast.logical.hypotheticalInterruptions, 0);
      assert.deepEqual(restoredForecast.owners, first.owners);
      assert.ok(select(profiles.candidate, 'second_simulated_promotion'));
      assert.equal(select({ version: 'unapproved', identity: 'different' }, 'ambiguous_identity'), false);
      const escalation = await sampleAt(345, 'escalate_under_candidate');
      assert.ok(escalation.events.some(row => row.transition === 'UPDATE'));
      assert.ok(escalation.events.some(row => row.transition === 'ESCALATE'));
      assert.equal(escalation.logical.openings, 0);
      assert.ok(escalation.owners.some(row => row.kind === 'DETECTION' && row.state === 'active'));
      assert.ok(select(profiles.baseline, 'rollback'));
      connection.close();
      connection = new DatabaseSync(host.file);
      assert.equal((await sampleAt(345, 'restart_and_repeat_after_rollback')).logical.openings, 0);
      const recovered = await sampleAt(400, 'confirmed_recovery');
      assert.ok(recovered.owners.some(row => row.state === 'closed'));
      const recurrence = await sampleAt(945, 'genuine_recurrence');
      assert.ok(recurrence.logical.hypotheticalInterruptions > 0);
      const rotated = await sampleAt(1500, 'namespace_rebase');
      assert.equal(rotated.logical.rotated, true);
      assert.equal(rotated.logical.hypotheticalInterruptions, 0);
      assert.equal((await sampleAt(1500, 'repeat_rebase')).logical.hypotheticalInterruptions, 0);
      for (const row of output.filter(row => row.eligible)) {
        assert.equal(new Set(row.eligible.map(entry => entry.finding)).size, row.eligible.length);
      }
      evaluations.push(output);
    } finally { connection.close(); }
    if (!repeat) { await python.stop(); await python.start(); }
  }
  assert.deepEqual(evaluations[0], evaluations[1]);
  const decision = evaluations[0];
  const digest = require('node:crypto').createHash('sha256').update(JSON.stringify(decision)).digest('hex');
  const rows = decision.map(({ owners, events, consumed, eligible, ...item }) => ({
    ...item, owners: owners?.length, transitions: events?.length, consumed: consumed?.length, eligible: eligible?.length
  }));
  console.log('PHASE9_OWNERSHIP ' + JSON.stringify({ runs: 2, digest, duplicateLogicalOpenings: 0,
    duplicateHypotheticalOpenings: 0, actualNotifications: 0, rows }));
});
