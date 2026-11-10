// Test planning, evidence and recovery with temporary in-memory data instead of real external actions.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { EventEmitter } = require('node:events');
const ts = require('typescript');
const root = path.resolve(__dirname, '..');
const shared = {};
function load(filename, dependencies, globals = {}) {
  const exports = {};
  const code = ts.transpileModule(fs.readFileSync(path.join(root, filename), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true } }).outputText;
  vm.runInNewContext(code, { exports, require: name => Object.hasOwn(dependencies, name) ? dependencies[name] : require(name), globalThis: shared, process, console, AbortController, Buffer, URL, Request, setTimeout, ...globals });
  return exports;
}
const records = new Map();
const files = new Map();
const store = {
  getRecord: (_bucket, id) => records.has(id) ? structuredClone(records.get(id)) : undefined,
  putRecord: (_bucket, id, value) => records.set(id, structuredClone(value)),
  listRecords: () => Array.from(records.values(), value => structuredClone(value)),
  redactSecrets: text => text, searchKnowledge: () => [], memoryContext: () => '',
};
const security = load('lib/controlSecurity.ts', {});
const evidence = load('lib/agentEvidence.ts', {});
let toolCalls = 0;
let crash = false;
let requestedPlan;
const runner = load('lib/agentRunner.ts', {
  './assistantStore': store, './controlSecurity': security, './agentEvidence': evidence,
  './agentReports': { exportReport: async filename => { assert.ok(files.size > 0); return `/fixture/Documents/ULTRON Reports/${filename}`; }, exportedReportPath: async filename => `/fixture/Documents/ULTRON Reports/${filename}` },
  './agentProvider': { agentCompletion: async (_prompt, _input, json) => json ? JSON.stringify(requestedPlan) : '# Fixture report\nNo external facts asserted.' },
  'node:fs/promises': { mkdir: async () => {}, writeFile: async (filename, text) => { if (files.has(filename)) throw new Error('File exists'); files.set(filename, text); }, readFile: async filename => files.get(filename) },
  './systemActions': { executeSystemAction: async () => { toolCalls++; if (crash) throw new Error('Uncertain external outcome'); return { ok: true, message: 'Launch accepted' }; } },
});
async function approve(task) {
  const pending = await runner.executeTaskStep(task.id, 'http://127.0.0.1:3000');
  assert.equal(pending.status, 'approval');
  return runner.executeTaskStep(task.id, 'http://127.0.0.1:3000', pending.approval.token);
}
async function main() {
  const reportFiles = new Map();
  const reportSource = path.join(process.cwd(), '.ultron', 'reports', 'fixture.md');
  reportFiles.set(reportSource, '# Export fixture');
  let redirectFolder = false;
  const reports = load('lib/agentReports.ts', {
    'node:os': { homedir: () => path.parse(root).root },
    'node:fs/promises': {
      mkdir: async () => {},
      realpath: async filename => redirectFolder && filename.endsWith('ULTRON Reports') ? path.join(root, 'outside-fixture') : filename,
      readFile: async filename => { if (!reportFiles.has(filename)) throw new Error('Missing fixture'); return reportFiles.get(filename); },
      writeFile: async (filename, text, options) => { assert.equal(options.flag, 'wx'); if (reportFiles.has(filename)) throw new Error('File exists'); reportFiles.set(filename, text); },
    },
  });
  const reportDestination = await reports.exportReport('fixture.md');
  assert.equal(reportFiles.get(reportDestination), '# Export fixture');
  await assert.rejects(() => reports.exportReport('fixture.md'), /File exists/);
  await assert.rejects(() => reports.exportReport('../escape.md'), /Invalid/);
  redirectFolder = true;
  await assert.rejects(() => reports.exportedReportPath('fixture.md'), /outside Documents/);
  assert.equal(evidence.toolEvidence('openApp', { ok: true }).state, 'acknowledged');
  assert.equal(evidence.toolEvidence('browserClick', { ok: true, browser: { url: 'https://example.com' } }).state, 'acknowledged');
  assert.equal(evidence.toolEvidence('browserOpen', { ok: true, browser: { url: 'https://example.com', title: 'Example' } }).state, 'verified');
  assert.throws(() => evidence.toolEvidence('openApp', { ok: false }));
  assert.throws(() => runner.validateAgentPlan({ title: 'Bad', questions: [], steps: [{ title: 'Shell', tool: 'shell', args: {} }] }));
  requestedPlan = { title: 'Fixture', questions: [], steps: [{ title: 'Read local KB', tool: 'knowledgeSearch', args: { query: 'fixture' } }, { title: 'Write report', tool: 'makeReport', args: {} }] };
  const task = await runner.createTask('Fixture report');
  assert.equal(task.status, 'review');
  runner.changeTask(task.id, 'run');
  let current = await approve(task);
  assert.equal(current.index, 1);
  assert.equal(current.steps[0].evidence.state, 'verified');
  current = await approve(current);
  assert.equal(current.status, 'completed');
  assert.equal(current.steps[1].evidence.state, 'verified');
  assert.equal(files.size, 1);
  requestedPlan = { title: 'Export fixture', questions: [], steps: [{ title: 'Export', tool: 'exportReport', args: {} }, { title: 'Open', tool: 'openReport', args: {} }] };
  const exported = await runner.createTask('Export saved report');
  exported.report = current.report;
  store.putRecord('task', exported.id, exported);
  runner.changeTask(exported.id, 'run');
  current = await approve(exported);
  assert.equal(current.steps[0].evidence.state, 'verified');
  current = await approve(current);
  assert.equal(current.status, 'completed');
  assert.equal(current.steps[1].evidence.state, 'acknowledged');
  toolCalls = 0;
  requestedPlan = { title: 'Launch', questions: [], steps: [{ title: 'Notepad', tool: 'openApp', args: { app: 'notepad' } }] };
  const app = await runner.createTask('Open Notepad');
  runner.changeTask(app.id, 'run');
  current = await approve(app);
  assert.equal(current.steps[0].evidence.state, 'acknowledged');
  assert.equal(toolCalls, 1);
  await assert.rejects(() => runner.executeTaskStep(app.id, 'http://127.0.0.1:3000', 'reused-token'));
  const failed = await runner.createTask('Uncertain app launch');
  runner.changeTask(failed.id, 'run'); crash = true;
  current = await approve(failed);
  assert.equal(current.status, 'failed');
  assert.throws(() => runner.changeTask(failed.id, 'run'), /automatic replay is blocked/);
  assert.equal(toolCalls, 2);
  const stale = await runner.createTask('Restart fixture');
  runner.changeTask(stale.id, 'run');
  const spawns = [];
  const worker = load('lib/agentWorker.ts', {
    './assistantStore': store, './agentRunner': runner,
    'node:child_process': { spawn: (_command, _args, options) => { spawns.push(options); const child = new EventEmitter(); child.exitCode = null; child.unref = () => {}; return child; } },
  });
  worker.ensureAgentWorker('http://127.0.0.1:3000');
  assert.equal(runner.taskById(stale.id).status, 'paused');
  assert.equal(spawns.length, 1);
  assert.equal(spawns[0].env.GEMINI_API_KEY, undefined);
  worker.ensureAgentWorker('http://127.0.0.1:3000');
  assert.equal(spawns.length, 1);
  assert.throws(() => worker.assertWorkerRequest(new Request('http://127.0.0.1:3000/api/agent/worker')));
  worker.assertWorkerRequest(new Request('http://127.0.0.1:3000/api/agent/worker', { headers: { authorization: `Bearer ${spawns[0].env.ULTRON_WORKER_TOKEN}` } }));
  runner.changeTask(stale.id, 'run');
  await worker.dispatchAgentTasks('http://127.0.0.1:3000');
  assert.equal(runner.taskById(stale.id).status, 'approval');
  assert.equal(toolCalls, 2);
  runner.pauseActiveTasks();
  assert.equal(runner.taskById(stale.id).status, 'paused');
  assert.throws(() => worker.ensureAgentWorker('https://example.com'));
  console.log('Agent checks passed: permissions, evidence, report read-back, replay blocking, restart recovery, worker authentication, background dispatch, pause-all. No real apps, network APIs or microphone used.');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
