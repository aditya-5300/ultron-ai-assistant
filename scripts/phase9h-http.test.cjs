const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { createHash, randomBytes } = require('node:crypto');
const { once } = require('node:events');
const http = require('node:http');
const path = require('node:path');
const { createInterface } = require('node:readline');
const { test } = require('node:test');
const { setTimeout: wait } = require('node:timers/promises');
const { fixture, portNumber, repo, seed, until } = require('./phase8b-fixture.cjs');
const { checkpoint, migrateObservability } = require('../lib/health/shadow/store.cjs');
const { exportReplay } = require('../lib/health/shadow/export.cjs');
const { createClient } = require('../lib/health/shadow/client.cjs');
const { verifyMetadata, verifyResponse } = require('../lib/health/shadow/contract.cjs');
const config = require('../docs/phase9h-reference/phase9h_config.json');

const digest = data => createHash('sha256').update(JSON.stringify(data)).digest('hex');
const errorCode = error => /^[A-Z][A-Z0-9_]{1,40}$/.test(error?.code ?? '') ? error.code : 'CHECK_FAILED';
const span = request => (Date.parse(request.telemetry.as_of) - Date.parse(request.telemetry.window_start)) / 1000;

async function observe(caseId, action, history, context, requests) {
  const original = http.request;
  const tracked = [];
  http.request = function (options, ...args) {
    const req = original.call(this, options, ...args);
    if (options.port !== context.port) return req;
    const start = performance.now();
    const declared = options.headers?.['Content-Length'] ?? 0;
    const entry = { caseId, request: requests.length + 1, route: options.path, startUtc: new Date().toISOString(),
      declaredBytes: declared, records: declared ? history.telemetry.records.length : null,
      historySeconds: declared ? span(history) : null, status: null, complete: false, error: null,
      responseAborted: false, timeout: false, reusedSocket: false };
    requests.push(entry);
    tracked.push(new Promise(resolve => req.once('close', resolve)));
    const elapsed = () => performance.now() - start;
    req.on('socket', socket => {
      entry.reusedSocket = req.reusedSocket;
      socket.once('connect', () => {
        entry.connectMs = elapsed();
        entry.peer = socket.localPort;
      });
    });
    req.on('finish', () => { entry.uploadHandoffMs = elapsed(); });
    req.on('response', response => {
      entry.headersMs = elapsed();
      entry.status = response.statusCode;
      entry.connection = response.headers.connection === 'close' ? 'close' : 'not-close';
      response.on('end', () => {
        entry.complete = response.complete;
        entry.responseMs = elapsed();
      });
      response.on('aborted', () => { entry.responseAborted = true; });
      response.on('error', error => { entry.error = errorCode(error); });
      response.on('close', () => { entry.complete ||= response.complete; });
    });
    req.on('timeout', () => { entry.timeout = true; });
    req.on('error', error => { entry.error = errorCode(error); entry.timeout ||= entry.error === 'TIMEOUT'; });
    req.on('close', () => { entry.closeMs = elapsed(); });
    return req;
  };
  try {
    return { value: await action(), error: null };
  } catch (error) {
    return { error: errorCode(error) };
  } finally {
    http.request = original;
    await Promise.all(tracked);
    for (const entry of requests.filter(item => item.caseId === caseId)) {
      entry.listenerExit = context.child.exitCode;
      entry.listenerSignal = context.child.signalCode;
    }
  }
}

function raw(port, token, route, body, expectContinue = false) {
  return new Promise((resolve, reject) => {
    const headers = { 'X-ULTRON-Service-Token': token };
    if (body) Object.assign(headers, { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) });
    if (expectContinue) headers.Expect = '100-continue';
    const req = http.request({ host: '127.0.0.1', port, path: route, agent: false,
      method: body ? 'POST' : 'GET', headers }, response => {
      const chunks = [];
      response.on('data', chunk => chunks.push(chunk));
      response.on('error', reject);
      response.on('end', () => {
        try {
          if (!response.complete) throw new Error('Incomplete fixture response');
          resolve({ status: response.statusCode, body: JSON.parse(Buffer.concat(chunks)) });
        } catch (error) { reject(error); }
      });
    });
    req.on('error', reject);
    req.setTimeout(config.fixture_network_idle_ms, () => req.destroy(Object.assign(new Error(), { code: 'TIMEOUT' })));
    if (expectContinue) {
      req.once('continue', () => req.destroy(Object.assign(new Error(), { code: 'UNEXPECTED_CONTINUE' })));
      req.flushHeaders();
    } else req.end(body);
  });
}

function requestsFrom(store) {
  migrateObservability(store.db);
  const saved = checkpoint(store.db);
  const start = Date.UTC(2025, 1, 1);
  function request(end, first) {
    const result = exportReplay(store.db, 'seed-' + end, saved.installation, { ...saved, start_id: 'seed-' + first });
    assert.equal(result.request.telemetry.records.length, end - first + 1);
    return result.request;
  }
  seed(store.db, config.max_records, start, 15, true);
  const below = request(479, 0), maximum = request(config.max_records - 1, 0);
  store.db.exec('DELETE FROM samples');
  seed(store.db, 1440, start, 30, true);
  const nearHistory = request(1439, 0);
  function shifted(seconds) {
    const request = structuredClone(nearHistory);
    const window = request.telemetry;
    const earlier = value => new Date(Date.parse(value) - seconds * 1000).toISOString();
    window.window_start = earlier(window.window_start);
    const [first, second] = window.records;
    first.sample_time = earlier(first.sample_time);
    for (const reading of Object.values(first.metrics)) {
      if (reading.observation_time) reading.observation_time = earlier(reading.observation_time);
    }
    second.elapsed_seconds += seconds;
    return request;
  }
  assert.equal(span(nearHistory), config.max_history_seconds - 30);
  return { below, maximum, nearHistory, exactHistory: shifted(30), aboveHistory: shifted(31) };
}

const python = process.env.ULTRON_PHASE8B_PYTHON;
test('five independent authenticated Phase 6 request-boundary repetitions', { timeout: 420000 }, async () => {
  assert.ok(python, 'Set ULTRON_PHASE8B_PYTHON');
  let failed = 0;
  for (let index = 1; index <= config.repetitions; index++) {
    const cleanup = [];
    const target = fixture({ after: callback => cleanup.push(callback) });
    const requests = [], traces = [], cases = [];
    const port = await portNumber();
    const token = randomBytes(32).toString('base64url');
    const started = performance.now();
    const child = spawn(python, [path.join(repo, 'scripts/phase9h-observed-listener.py'), '--port', String(port)],
      { cwd: repo, windowsHide: true, stdio: ['ignore', 'pipe', 'ignore'],
        env: { ...process.env, PYTHONPATH: path.join(repo, 'ml/src'), ULTRON_INFERENCE_TOKEN: token } });
    const exited = once(child, 'close').catch(() => []);
    const output = createInterface({ input: child.stdout });
    output.on('line', line => {
      const row = JSON.parse(line);
      traces.push(row);
    });
    const context = { port, child };
    let readyMs = null, failure = null, stopMs = null;
    try {
      await until(async () => {
        if (child.exitCode !== null) throw new Error('Listener exited before readiness');
        return raw(port, token, '/healthz').then(response => response.status === 200 && response.body.status === 'ok', () => false);
      }, 'Phase 9-H verified readiness', 15000);
      readyMs = performance.now() - started;
      const client = createClient({ port, token });
      const { below, maximum, nearHistory, exactHistory, aboveHistory } = requestsFrom(target.store);
      const body = JSON.stringify(maximum), cap = config.body_bytes;
      assert.ok(Buffer.byteLength(body) < cap - config.near_cap_delta_bytes && Buffer.byteLength(body) === body.length);
      const good = request => JSON.stringify(request);
      const padded = size => body.padEnd(size, ' ');
      const valid = async (request, payload) => {
        const response = await client.infer(payload);
        verifyResponse(response, request);
        return digest(response);
      };
      const casesToRun = [
        ['below_cap', below, good(below), null],
        ['max_records', maximum, body, null],
        ['near_cap', maximum, padded(cap - config.near_cap_delta_bytes), null],
        ['exact_cap', maximum, padded(cap), null],
        ['above_cap_client', maximum, padded(cap + 1), 413],
        ['near_12_hours', nearHistory, good(nearHistory), null],
        ['exact_12_hours', exactHistory, good(exactHistory), null],
        ['above_12_hours', aboveHistory, good(aboveHistory), 422],
        ['repeat_valid', below, good(below), null],
        ['above_cap_raw_full_upload', maximum, padded(cap + 1), 413, 'full'],
        ['above_cap_raw_header_probe', maximum, padded(cap + 1), 413, 'headers']
      ];
      assert.deepEqual(casesToRun.map(row => row[0]), config.cases);
      let stable = null, maxDigest = null;
      for (const [id, request, payload, rejection, probe] of casesToRun) {
        const result = await observe(id, () => probe
          ? raw(port, token, '/v1/infer', payload, probe === 'headers') : valid(request, payload), request, context, requests);
        const inference = requests.find(item => item.caseId === id && item.route === '/v1/infer');
        let pass = rejection === null
          ? !result.error && inference?.status === 200 && inference.complete && !inference.reusedSocket
          : probe ? !result.error && result.value.status === rejection && result.value.body.error === 'REQUEST_TOO_LARGE'
            && inference?.complete : result.error === 'CONTRACT_REJECTED' && inference?.status === rejection;
        if (id === 'below_cap' && pass) stable = result.value;
        if (id === 'repeat_valid') pass &&= result.value === stable;
        if (id === 'max_records' && pass) maxDigest = result.value;
        if (id === 'near_cap' || id === 'exact_cap') pass &&= result.value === maxDigest;
        const category = pass ? 'PASS' : (result.error === 'TIMEOUT' || inference?.timeout) ? 'TIMEOUT' :
          child.exitCode !== null ? 'SERVICE_EXIT' :
            ['ECONNRESET', 'UNAVAILABLE', 'EPIPE'].includes(result.error) || inference?.responseAborted ? 'TRANSPORT' : 'CONTRACT';
        const row = { id, category, status: inference?.status ?? null, error: result.error, complete: inference?.complete ?? false,
          bytes: Buffer.byteLength(payload), records: request.telemetry.records.length, historySeconds: span(request) };
        if (rejection !== null || !pass) {
          const recovery = await observe(id + '_recovery', async () => {
            assert.equal(child.exitCode, null);
            const metadata = await raw(port, token, '/v1/metadata');
            assert.equal(metadata.status, 200);
            verifyMetadata(metadata.body);
            return valid(below, good(below));
          }, below, context, requests);
          row.recovery = !recovery.error && recovery.value === stable && child.exitCode === null;
          row.recoveryError = recovery.error;
          if (!row.recovery) failed++;
        }
        if (!pass && (probe !== 'full' || category !== 'TRANSPORT')) failed++;
        cases.push(row);
      }
      await wait(40);
    } catch (error) {
      failure = errorCode(error);
      failed++;
    } finally {
      const aliveBeforeStop = child.exitCode === null && child.signalCode === null;
      const stop = performance.now();
      if (aliveBeforeStop) {
        if (process.platform === 'win32') {
          const killer = spawn('taskkill.exe', ['/PID', String(child.pid), '/T', '/F'],
            { stdio: 'ignore', windowsHide: true });
          await once(killer, 'exit');
        } else child.kill();
      } else failed++;
      await exited;
      stopMs = performance.now() - stop;
      for (const action of cleanup) await action();
      console.log('PHASE9H ' + JSON.stringify({ index, readyMs, stopMs, aliveBeforeStop, exitCode: child.exitCode,
        exitSignal: child.signalCode, requests, traces, cases, failure, actualNotifications: 0 }));
    }
  }
  assert.equal(failed, 0, 'Required request, recovery, or listener checks failed; inspect all PHASE9H rows');
});
