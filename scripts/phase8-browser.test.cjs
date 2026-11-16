const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const os = require('node:os');
const { spawn } = require('node:child_process');
const { once } = require('node:events');
const net = require('node:net');
const { chromium } = require('playwright-core');
const { createStore } = require('../lib/health/core.cjs');
const { migrateObservability, status } = require('../lib/health/shadow/store.cjs');
const { setTimeout: wait } = require('node:timers/promises');

test('production dashboard renders isolated read-only records and lifecycle pagination', {
  skip: !process.env.ULTRON_PHASE8_TEST_BROWSER, timeout: 90000
}, async context => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'ultron-phase8-ui-'));
  const store = createStore(root);
  const { db } = store;
  migrateObservability(db);
  const listener = net.createServer();
  listener.listen(0, '127.0.0.1');
  await once(listener, 'listening');
  const port = listener.address().port;
  await new Promise(resolve => listener.close(resolve));
  const project = path.resolve(__dirname, '..');
  const links = ['.next', 'node_modules'];
  for (const directory of links) fs.symlinkSync(path.join(project, directory), path.join(root, directory), 'junction');
  const server = `
    const next = require('next');
    const http = require('node:http');
    const app = next({ dev: false, dir: process.cwd(), hostname: '127.0.0.1', port: Number(process.argv[1]) });
    app.prepare().then(() => {
      http.createServer(app.getRequestHandler()).listen(Number(process.argv[1]), '127.0.0.1');
    }).catch(error => { console.error(error); process.exitCode = 1; });
  `;
  const child = spawn(process.execPath, ['-e', server, String(port)],
    { cwd: root, env: { ...process.env, ULTRON_INFERENCE_MODE: 'shadow', ULTRON_INFERENCE_TOKEN: '' }, stdio: ['ignore', 'ignore', 'pipe'], windowsHide: true });
  let logs = '';
  child.stderr.on('data', data => { logs = (logs + data).slice(-3000); });
  context.after(async () => {
    if (child.exitCode === null && child.signalCode === null) { const exited = once(child, 'exit'); child.kill(); await exited; }
    db.close();
    for (const directory of links) fs.unlinkSync(path.join(root, directory));
    const directory = path.join(root, '.ultron');
    for (const name of fs.readdirSync(directory)) fs.unlinkSync(path.join(directory, name));
    fs.rmdirSync(directory);
    fs.rmdirSync(root);
  });
  const url = `http://127.0.0.1:${port}/monitor/shadow`;
  for (let attempt = 0; attempt < 80; attempt++) {
    if (await fetch(url).then(response => response.ok).catch(() => false)) break;
    if (child.exitCode !== null || attempt === 79) assert.fail('Fixture server did not start: ' + logs);
    await wait(250);
  }
  const browser = await chromium.launch({ executablePath: process.env.ULTRON_PHASE8_TEST_BROWSER, headless: true });
  try {
    const page = await browser.newPage();
    const api = /\/api\/monitor\/shadow\?/;
    await page.route(api, async route => {
      if (route.request().url().includes('view=status')) await wait(650);
      await route.continue();
    });
    await page.goto(url);
    await page.getByText('Loading inference status…').waitFor();
    await page.getByText('INSUFFICIENT_HISTORY', { exact: true }).waitFor();
    await page.unroute(api);
    const now = Date.now();
    const addFinding = db.prepare('INSERT INTO shadow_findings VALUES (?,?,?,?,?,?,?,?,?,?)');
    const addTransition = db.prepare('INSERT INTO shadow_transitions VALUES (?,?,?,?,?)');
    for (let index = 0; index < 11; index++) {
      const id = index.toString(16).padStart(64, '0');
      const timestamp = new Date(now - index * 1000).toISOString();
      const item = { subsystem: 'process', finding_type: 'FORECAST', state: index === 1 ? 'closed' : 'active',
        severity: 'early_warning', first_at: timestamp, decision_at: timestamp, definition_version: 'phase5-targets-v1',
        evidence: { quality: 'fresh' }, horizon_seconds: 1800 };
      addFinding.run(id, 'fixture', 'process', item.state, now - index * 1000, now, now, 'python-shadow', 'phase6-approved-v1', JSON.stringify(item));
      addTransition.run(id, 'fixture', id, now - index * 1000, JSON.stringify({ ...item, transition: index === 1 ? 'CLOSE' : 'OPEN' }));
      db.prepare('INSERT INTO shadow_eligibility VALUES (?,?,?,?,?,?)').run(id, id, 'fixture', now, 'phase8-offline-v1', 'HISTORICAL_BOOTSTRAP');
    }
    status(db, 'UNAVAILABLE', 'UNAVAILABLE');
    await page.getByRole('button', { name: 'Refresh view' }).click();
    await page.getByText('UNAVAILABLE', { exact: true }).waitFor();
    status(db, 'DEGRADED', 'SQLITE_BUSY');
    await page.getByRole('button', { name: 'Refresh view' }).click();
    await page.getByText('DEGRADED', { exact: true }).waitFor();
    await page.getByText(/Historical bootstrap/).first().waitFor();
    await page.getByText(/active in replay/).first().waitFor();
    await page.getByText(/closed in replay/).first().waitFor();
    await page.getByRole('button', { name: 'Process responsiveness · FORECAST' }).nth(1).click();
    await page.getByText('Process responsiveness · FORECAST · CLOSE', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Next', exact: true }).first().click();
    await page.getByText('Page 2', { exact: true }).waitFor();
    await page.getByRole('heading', { name: /Not Used for Alerts/ }).waitFor();
    assert.equal(await page.getByRole('button', { name: /enable|remediate|restart|notify/i }).count(), 0);
    assert.equal((await fetch(`http://127.0.0.1:${port}/api/monitor/shadow`)).status, 403);
    await page.route(api, route => route.request().url().includes('view=status') ?
      route.fulfill({ status: 200, json: { state: 'DISABLED', compatibility: null } }) : route.continue());
    await page.getByRole('button', { name: 'Refresh view' }).click();
    await page.getByText('DISABLED', { exact: true }).waitFor();
    await page.unroute(api);
    await page.route(api, route => route.fulfill({ status: 503, json: {} }));
    await page.getByRole('button', { name: 'Refresh view' }).click();
    await page.getByRole('alert').filter({ hasText: /Could not load/ }).waitFor();
    console.log('PHASE8_BROWSER fixtures: loading, disabled, unavailable, degraded, active/closed, lifecycle, history, pagination, advisory and error');
  } finally { await browser.close(); }
});
