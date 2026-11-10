// Feed generated audio to Windows speech and check its transcripts without executing their commands.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { execFileSync } = require('node:child_process');
const ts = require('typescript');
const root = path.resolve(__dirname, '..');
function load(filename, dependencies = {}) {
  const exports = {};
  const code = ts.transpileModule(fs.readFileSync(path.join(root, filename), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true } }).outputText;
  vm.runInNewContext(code, { exports, require: name => Object.hasOwn(dependencies, name) ? dependencies[name] : require(name) });
  return exports;
}
if (process.platform !== 'win32') {
  console.log('SKIP: synthetic Windows speech checks require Windows and an installed English recognizer.');
} else {
  const parser = load('lib/voiceCommands.ts', { './appCatalog': load('lib/appCatalog.ts') });
  const controls = load('lib/voiceControl.ts');
  let count = 0;
  for (const mode of ['-SelfTest', '-DictationSelfTest']) {
    const output = execFileSync(path.join(process.env.SystemRoot || 'C:\\Windows', 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe'), ['-NoProfile', '-NonInteractive', '-File', path.join(root, 'scripts', 'voice-listener.ps1'), mode], { encoding: 'utf8', timeout: 90000, windowsHide: true, maxBuffer: 1024 * 1024 });
    const events = output.trim().split(/\r?\n/).map(line => JSON.parse(line));
    assert.ok(events.some(event => event.type === 'test'), `No success event for ${mode}`);
    for (const event of events.filter(event => event.type === 'command')) {
      assert.ok(controls.voiceControl(event.text) || parser.isAgentCommand(event.text) || parser.parseVoicePlan(event.text), `Recognized transcript cannot be routed: ${event.text}`);
      count++;
    }
    console.log(`${mode}: speech engine test and routing of emitted transcripts passed.`);
  }
  console.log(`PASS: ${count} generated-audio transcripts routed. No microphone access, speech playback, tool execution, app launch, phone access or model API call. Free-text wording can differ from the synthesized request; this does not establish live-user accuracy.`);
}
