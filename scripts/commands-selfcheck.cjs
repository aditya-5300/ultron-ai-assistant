// Use stand-in tools to check command routing and permissions without changing the user's computer.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { EventEmitter } = require('node:events');
const ts = require('typescript');
const root = path.resolve(__dirname, '..');
function load(filename, dependencies = {}, globals = {}) {
  const exports = {};
  const code = ts.transpileModule(fs.readFileSync(path.join(root, filename), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true } }).outputText;
  vm.runInNewContext(code, { exports, require: name => Object.hasOwn(dependencies, name) ? dependencies[name] : require(name), process, console, URL, Response, ReadableStream, TextEncoder, AbortController, AbortSignal, setTimeout, clearTimeout, setInterval, clearInterval, ...globals });
  return exports;
}
const catalog = load('lib/appCatalog.ts');
const parser = load('lib/voiceCommands.ts', { './appCatalog': catalog });
const grammar = require('../lib/voiceGrammar.json');
for (const command of grammar.fixedCommands) assert.ok(parser.parseVoicePlan(command), command);
for (const [id, app] of Object.entries(catalog.APP_CATALOG)) {
  for (const alias of app.aliases) {
    assert.equal(parser.parseVoiceCommand(`Ultron open ${alias}`).payload.app, id);
    assert.equal(parser.parseVoiceCommand(`Ultron close ${alias}`).payload.app, id);
  }
}
const examples = [
  ['open new tab', 'browserNewTab'], ['open Documents', 'openFolder'], ['open D drive', 'openDrive'],
  ['open File Manager and D drive', 'openDrive'], ['find files named report in documents', 'searchFiles'],
  ['open file "C:\\Users\\adity\\Documents\\notes.txt"', 'openFile'], ['summarize file notes.txt', 'summarizeFile'],
  ['browser open example dot com', 'browserOpen'], ['browser inspect', 'browserInspect'], ['browser back', 'browserBack'],
  ['browser click one', 'browserClick'], ['browser fill two with hello', 'browserFill'],
  ['browser scroll up', 'browserScroll'], ['browser close', 'browserClose'],
  ['run a Defender quick scan', 'securityScan'], ['list phone devices', 'adbDevices'],
  ['phone screenshot', 'adbScreenshot'], ['phone open YouTube', 'adbOpenApp'], ['phone home', 'adbKey'],
  ['phone swipe up', 'adbSwipe'], ['phone tap 300 500', 'adbTap'], ['phone tap three hundred five hundred', 'adbTap'],
  ['phone tap three hundred comma five hundred', 'adbTap'], ['phone type hello', 'adbType'], ['show commands', 'commandHelp'],
];
for (const [command, action] of examples) assert.equal(parser.parseVoicePlan(`ULTRON, ${command}.`)[0].payload.action, action, command);
assert.equal(parser.parseVoiceCommand('browser click sixty').payload.nodeId, 60);
assert.equal(parser.parseVoiceCommand('browser click sixty one'), null);
assert.equal(parser.parseVoiceCommand('browser fill zero with hello'), null);
assert.equal(parser.parseVoiceCommand('browser open example dot com').payload.url, 'https://example.com');
assert.equal(parser.parseVoiceCommand('phone tap twenty one'), null);
assert.equal(parser.parseVoiceCommand('phone tap three hundred five hundred').payload.x, 300);
assert.equal(parser.parseVoiceCommand('phone tap three hundred five hundred').payload.y, 500);
assert.equal(parser.parseVoiceCommand('phone tap one thousand two hundred comma five hundred').payload.x, 1200);
assert.equal(parser.parseVoicePlan('phone type hello then open Notepad')[0].payload.text, 'hello then open Notepad');
assert.equal(parser.parseVoicePlan('browser fill two with hello then open Notepad')[0].payload.text, 'hello then open Notepad');
assert.equal(parser.parseVoicePlan('delete everything'), null);
assert.equal(parser.parseVoicePlan('ULTRON, shutdown the PC.')[0].payload.kind, 'shutdown');
assert.equal(parser.parseVoicePlan('restart my computer')[0].confirm, true);
assert.equal(parser.parseVoicePlan('cancel restart')[0].payload.kind, 'restart');
for (const phrase of ['shutdown /f', 'restart the PC then open Notepad', 'open Notepad then shut down the PC']) {
  assert.equal(parser.parseVoicePlan(phrase), null);
  assert.equal(parser.isSystemCommand(phrase), true);
}
assert.equal(parser.parseVoicePlan('Ultron open YouTube and Ultron open the Notepad').length, 2);
assert.equal(parser.parseVoicePlan('Ultron open YouTube and hey Ultron open Notepad')[1].payload.app, 'notepad');
assert.equal(parser.parseVoicePlan(Array(7).fill('open Notepad').join(' then ')), null);
for (const goal of ['research SSD versus HDD', 'make a report about the attached document', 'plan a trip', 'book me a ticket']) assert.equal(parser.isAgentCommand(goal), true);
assert.equal(parser.isAgentCommand('open Notepad'), false);
const security = load('lib/controlSecurity.ts');
const controls = load('lib/voiceControl.ts');
const cancellation = load('lib/responseCancellation.ts');
let receiver;
let planningSignal;
let waitForPlan = false;
const calls = [];
const browserCalls = [];
const voice = load('lib/desktopVoice.ts', {
  './voiceCommands': parser, './voiceControl': controls, './responseCancellation': cancellation, './controlSecurity': security,
  './agentRunner': {
    pauseActiveTasks: () => {},
    createTask: async (goal, _previous, signal) => {
      planningSignal = signal;
      if (waitForPlan) await new Promise((_resolve, reject) => signal.addEventListener('abort', () => reject(new Error('Cancelled fixture'))));
      return { id: 'fixture-task', title: goal, status: 'review', questions: [], steps: [] };
    },
  },
  './browserControl': { executeBrowserControl: async payload => {
    browserCalls.push(payload);
    if (payload.action === 'browserClick') { const permission = security.approvalFor(payload, 'Fixture element'); if (permission) return { ok: false, approval: permission }; }
    return { ok: true, message: 'Fixture browser result', browser: { url: 'https://example.com', title: 'Fixture', snapshot: 'snapshot-fixture', elements: [{ id: 1, label: 'Example', sensitive: false }], screenshot: '' } };
  } },
  './systemActions': { executeSystemAction: async payload => {
    calls.push(payload);
    if (payload.action === 'adbSelectDevice') return { ok: true, deviceId: payload.deviceId };
    if (payload.action === 'adbTap') {
      assert.equal(payload.deviceId, 'fixture-device');
      const permission = security.approvalFor(payload, 'Fixture phone'); if (permission) return { ok: false, approval: permission, deviceId: 'fixture-device' };
    }
    return { ok: true, message: 'Fixture result', text: 'No private data' };
  } },
  'node:fs': { promises: { mkdir: async () => {}, writeFile: async () => {} } },
  'node:readline': { createInterface: () => ({ on: (_event, callback) => { receiver = callback; } }) },
  'node:child_process': { spawn: () => {
    const child = new EventEmitter(); child.stdout = new EventEmitter(); child.stderr = new EventEmitter();
    child.stdin = new EventEmitter(); child.stdin.writable = true; child.stdin.write = () => {};
    child.stdin.end = () => { child.exitCode = 0; child.emit('close'); };
    child.kill = () => { child.exitCode = 0; child.emit('close'); };
    return child;
  } },
});
const tick = () => new Promise(resolve => setImmediate(resolve));
async function checkPower() {
  // Both the clock and Windows executable are simulated; this test cannot power off a PC.
  let now = 100_000;
  let sequence = 0;
  const timers = new Map();
  const launches = [];
  const globals = {
    process: { platform: 'win32', env: { SystemRoot: 'C:\\Windows' } },
    Date: class extends Date { static now() { return now; } },
    setTimeout: (callback, delay) => { timers.set(++sequence, { callback, deadline: now + delay }); return sequence; },
    clearTimeout: timer => timers.delete(timer),
  };
  const powerSecurity = load('lib/controlSecurity.ts', {}, globals);
  const { executePowerAction: power } = load('lib/powerActions.ts', {
    './controlSecurity': powerSecurity,
    'node:child_process': { execFile: (file, args, options, callback) => {
      launches.push({ file, args: Array.from(args), timeout: options.timeout });
      callback(null, '', '');
    } },
  }, globals);
  async function advance(elapsed) {
    now += elapsed;
    for (const [id, timer] of timers) if (timer.deadline <= now) { timers.delete(id); timer.callback(); }
    await tick();
  }
  const shutdown = { action: 'schedulePower', kind: 'shutdown' };
  const restart = { action: 'schedulePower', kind: 'restart' };
  const first = power(shutdown);
  assert.ok(first.approval);
  assert.equal(timers.size, 0);
  assert.throws(() => power({ ...shutdown, force: true }));
  assert.throws(() => power({ ...restart, approvalToken: first.approval.token }), /does not match/);
  const cancelledToken = power(shutdown).approval.token;
  const scheduled = power({ ...shutdown, approvalToken: cancelledToken });
  assert.equal(scheduled.power.deadline, now + 60_000);
  assert.equal(power({ action: 'powerStatus' }).power.id, scheduled.power.id);
  assert.throws(() => power(restart), /already pending/);
  assert.throws(() => power({ action: 'cancelPower', kind: 'restart' }), /No matching/);
  assert.throws(() => power({ action: 'cancelPower', countdownId: 'stale-id' }), /No matching/);
  assert.equal(power({ action: 'cancelPower', countdownId: scheduled.power.id }).power, null);
  assert.throws(() => power({ ...shutdown, approvalToken: cancelledToken }), /already used/);
  await advance(60_000);
  assert.equal(launches.length, 0);
  const expired = power(restart).approval.token;
  await advance(60_001);
  assert.throws(() => power({ ...restart, approvalToken: expired }), /expired/);
  for (const [request, flag] of [[restart, '/r'], [shutdown, '/s']]) {
    const before = launches.length;
    power({ ...request, approvalToken: power(request).approval.token });
    await advance(59_999);
    assert.equal(launches.length, before);
    await advance(1);
    assert.equal(launches.length, before + 1);
    assert.deepEqual(launches.at(-1), { file: 'C:\\Windows\\System32\\shutdown.exe', args: [flag, '/t', '0'], timeout: 10_000 });
    assert.equal(power({ action: 'powerStatus' }).power, null);
  }
  power({ ...shutdown, approvalToken: power(shutdown).approval.token });
  await advance(66_000);
  assert.equal(launches.length, 2, 'A late countdown must not resume after interruption.');
  assert.match(power({ action: 'powerStatus' }).powerNotice, /expired/);
  console.log('PASS: power approval binding, one-use/expired tokens, countdown, scoped cancellation and non-forced Windows arguments (mock only).');
}
async function hear(command) { receiver(JSON.stringify({ type: 'command', text: `Ultron ${command}` })); await tick(); }
async function main() {
  await checkPower();
  await voice.setDesktopVoice(true);
  receiver(JSON.stringify({ type: 'ready', text: 'Fixture listener' }));
  await hear('browser inspect');
  await hear('browser click one');
  assert.equal(browserCalls.at(-1).snapshot, 'snapshot-fixture');
  const permission = voice.desktopVoiceState().approval;
  assert.ok(permission);
  await hear('approve');
  assert.equal(voice.desktopVoiceState().approval.token, permission.token);
  await voice.approveDesktopVoice(permission.token);
  assert.equal(browserCalls.at(-1).snapshot, 'snapshot-fixture');
  await assert.rejects(() => voice.approveDesktopVoice(permission.token));
  await hear('phone use device fixture-device');
  await hear('phone tap three hundred five hundred');
  assert.ok(voice.desktopVoiceState().approval);
  await voice.approveDesktopVoice(voice.desktopVoiceState().approval.token);
  assert.equal(calls.at(-1).x, 300); assert.equal(calls.at(-1).y, 500);
  await hear('find files named report in documents');
  const result = voice.desktopVoiceState().result;
  assert.equal(voice.desktopVoiceResult(result.id).data.text, 'No private data');
  assert.equal(voice.desktopVoiceState().result.data, undefined, 'Polling must not include large result bodies.');
  assert.throws(() => voice.desktopVoiceResult(result.id - 1));
  await hear('research SSD versus HDD and make a report');
  assert.equal(voice.desktopVoiceState().result.action, 'agentPlan');
  assert.equal(voice.desktopVoiceState().busy, false);
  waitForPlan = true;
  await hear('research cancellation fixture');
  assert.equal(voice.desktopVoiceState().busy, true);
  await hear('stop');
  assert.equal(planningSignal.aborted, true);
  assert.equal(voice.desktopVoiceState().enabled, true);
  await hear('stop listening');
  assert.equal(voice.desktopVoiceState().enabled, false);
  console.log(`PASS: ${grammar.fixedCommands.length} native fixed phrases, all app aliases, all command families, word-number parsing, browser snapshots, device selection, one-use approvals, on-demand results, voice plan creation and cancellation. Fixtures only; no real microphone, applications, scans, phone or model API used.`);
}
main().catch(error => { console.error(error); process.exitCode = 1; });
