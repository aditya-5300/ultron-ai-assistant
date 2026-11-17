const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { once } = require('node:events');
const { spawn } = require('node:child_process');
const { Worker, isMainThread, parentPort, workerData } = require('node:worker_threads');
const { setTimeout: wait } = require('node:timers/promises');
const config = require('../docs/phase9i-reference/phase9i_config.json');

async function stressWorker({ role, file, signal }) {
  const stop = new Int32Array(signal);
  let iterations = 0;
  const descriptor = role === 'io' ? fs.openSync(file, 'wx') : null;
  const buffer = descriptor === null ? null : Buffer.alloc(config.stress_io_bytes, 71);
  parentPort.postMessage({ ready: true });
  try {
    while (!Atomics.load(stop, 0)) {
      if (descriptor === null) {
        const end = performance.now() + config.stress_cpu_busy_ms;
        while (performance.now() < end) Math.sqrt(Math.random());
        Atomics.wait(stop, 0, 0, config.stress_cpu_pause_ms);
      } else {
        assert.equal(fs.writeSync(descriptor, buffer, 0, buffer.length, 0), buffer.length);
        fs.fsyncSync(descriptor);
        Atomics.wait(stop, 0, 0, config.stress_io_pause_ms);
      }
      iterations++;
      if (iterations % 32 === 0) await wait(0);
    }
  } finally {
    if (descriptor !== null) fs.closeSync(descriptor);
    parentPort.postMessage({ role, iterations });
    parentPort.close();
  }
}

async function runTest(output, label, testFile, pattern) {
  const file = fs.openSync(path.join(output, label + '.tap'), 'wx');
  const began = performance.now();
  try {
    const child = spawn(process.execPath, ['--test', '--test-concurrency=1', '--test-reporter=tap',
      '--test-name-pattern=' + pattern, testFile], { stdio: ['ignore', file, file], windowsHide: true });
    const [exitCode, signal] = await once(child, 'close');
    return { label, exitCode, signal, durationMs: performance.now() - began };
  } finally {
    fs.closeSync(file);
  }
}

async function stressed(output, label, legacy) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'ultron-9i-stress-'));
  const file = path.join(root, 'workload.bin');
  const signal = new SharedArrayBuffer(4);
  const stop = new Int32Array(signal);
  const workers = [];
  const summaries = [];
  let result;
  try {
    const roles = [...Array(config.stress_cpu_workers).fill('cpu'), 'io'];
    const ready = roles.map(role => {
      const worker = new Worker(__filename, { workerData: { role, file, signal } });
      workers.push({ worker, exited: once(worker, 'exit') });
      worker.on('message', row => { if (row.role) summaries.push(row); });
      return once(worker, 'message');
    });
    assert.ok((await Promise.all(ready)).every(([row]) => row.ready));
    result = await runTest(output, label, legacy, config.legacy_test);
  } finally {
    Atomics.store(stop, 0, 1);
    Atomics.notify(stop, 0);
    await Promise.all(workers.map(({ exited }) => exited));
    if (fs.existsSync(file)) fs.unlinkSync(file);
    fs.rmdirSync(root);
  }
  return { ...result, background: summaries.sort((left, right) => left.role.localeCompare(right.role)) };
}

async function main() {
  const output = path.resolve(process.argv[2]);
  const results = [];
  const legacy = 'scripts/phase8-shadow.test.cjs';
  const save = row => {
    results.push(row);
    fs.appendFileSync(path.join(output, 'execution.jsonl'), JSON.stringify(row) + '\n');
    console.log(row.label, 'exit=', row.exitCode);
  };
  fs.mkdirSync(output);
  for (let run = 1; run <= config.isolated_repetitions; run++) {
    save(await runTest(output, 'isolated-' + run, legacy, config.legacy_test));
  }
  save(await runTest(output, 'phase8d-paired', 'scripts/phase8d.test.cjs', config.paired_test));
  for (let run = 1; run <= config.stress_repetitions; run++) {
    save(await stressed(output, 'stress-' + run, legacy));
  }
  assert.equal(results.length, config.isolated_repetitions + 1 + config.stress_repetitions);
}

const task = isMainThread ? main() : stressWorker(workerData);
task.catch(error => { console.error(error); process.exitCode = 1; });
