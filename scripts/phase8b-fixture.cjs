const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawn } = require('node:child_process');
const { once } = require('node:events');
const net = require('node:net');
const http = require('node:http');
const { setTimeout: wait } = require('node:timers/promises');
const { randomBytes } = require('node:crypto');
const { createStore } = require('../lib/health/core.cjs');

const config = require('../docs/phase8b-reference/phase8b_config.json');
const repo = path.resolve(__dirname, '..');
function fixture(context) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'ultron-8b-'));
  const children = new Set();
  const spawnLocal = (program, args, env = {}, cwd = root) => {
    const child = spawn(program, args, { cwd, env: { ...process.env, ...env }, stdio: 'ignore', windowsHide: true });
    child.on('error', () => {});
    children.add(child);
    return child;
  };
  async function stop(child) {
    if (!child.pid || child.exitCode !== null || child.signalCode !== null) return;
    const exited = once(child, 'exit');
    if (process.platform === 'win32' && child.spawnargs.includes('ultron_health_ml.service')) {
      const killer = spawn('taskkill.exe', ['/PID', String(child.pid), '/T', '/F'], { stdio: 'ignore', windowsHide: true });
      await once(killer, 'exit');
    } else child.kill();
    await exited;
  }
  const store = createStore(root);
  context.after(async () => {
    for (const child of children) await stop(child);
    store.db.close();
    const temp = fs.realpathSync(os.tmpdir());
    const resolved = fs.realpathSync(root);
    if (path.dirname(resolved) !== temp || !path.basename(resolved).startsWith('ultron-8b-')) throw new Error('Unsafe fixture cleanup');
    fs.rmSync(resolved, { recursive: true });
  });
  return { root, store, spawnLocal, stop, file: path.join(root, '.ultron', 'health.sqlite') };
}

async function until(check, label, timeout = 50000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    const result = await check();
    if (result) return result;
    await wait(70);
  }
  throw new Error('Fixture timed out: ' + label);
}

async function portNumber() {
  const socket = net.createServer().listen(0, '127.0.0.1');
  await once(socket, 'listening');
  const { port } = socket.address();
  await new Promise(resolve => socket.close(resolve));
  return port;
}

async function service(fixture) {
  const port = await portNumber(), token = randomBytes(32).toString('base64url');
  let child;
  return { port, token, async start() {
    child = fixture.spawnLocal(process.env.ULTRON_PHASE8B_PYTHON, ['-m', 'ultron_health_ml.service', '--port', String(port)], {
      PYTHONPATH: path.join(repo, 'ml/src'), ULTRON_INFERENCE_TOKEN: token
    }, repo);
    await until(() => request(port, '/healthz').then(response => response.status === 200).catch(() => false), 'Python health');
  }, async stop() { if (child) await fixture.stop(child); } };
}

function request(port, endpoint, body, token) {
  return new Promise((resolve, reject) => {
    const headers = body ? { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body),
      'X-ULTRON-Service-Token': token } : {};
    const req = http.request({ host: '127.0.0.1', port, path: endpoint, agent: false,
      method: body ? 'POST' : 'GET', headers }, response => {
      const chunks = [];
      response.on('data', chunk => chunks.push(chunk));
      response.on('error', reject);
      response.on('end', () => resolve({ status: response.statusCode, body: Buffer.concat(chunks).toString() }));
    });
    req.setTimeout(body ? 15000 : 1000, () => req.destroy(new Error('Fixture HTTP timeout')));
    req.on('error', reject);
    req.end(body);
  });
}

function sample(id, timestamp, intervalSeconds = 15, critical = false, stalled = 0) {
  const time = new Date(timestamp).toISOString();
  return { id, time, intervalSeconds, cpuPct: 35, durationMs: 5, memory: { usedPct: 41 },
    volumes: [{ drive: 'C:', freePct: critical ? 2 : 50, freeBytes: 100, totalBytes: 5000 }],
    deepTime: time, processes: Array.from({ length: stalled }, () => ({ responding: false })),
    battery: { present: false }, disks: [], logs: [], coverage: { processes: 'ok: synthetic fixture' } };
}

function seed(db, count, start, step = 15, episodes = false, gapAt = Infinity, gap = 0) {
  const insert = db.prepare('INSERT INTO samples VALUES (?,?,?)');
  db.exec('BEGIN');
  for (let index = 0; index < count; index++) {
    const offset = index % 600;
    const stalled = episodes && offset >= 300 && offset < 360 ? offset < 320 ? 2 : 5 : 0;
    const row = sample('seed-' + index, start + (index * step + (index >= gapAt ? gap : 0)) * 1000, step, false, stalled);
    insert.run(row.id, row.time, JSON.stringify(row));
  }
  db.exec('COMMIT');
}

const evidence = (key, value) => console.log('PHASE8B ' + JSON.stringify({ key, value }));
module.exports = { config, repo, fixture, until, portNumber, service, request, sample, seed, evidence };
