import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { promises as fs } from "node:fs";
import path from "node:path";
import { createInterface } from "node:readline";
import { isAgentCommand, parseVoicePlan, normalizeCommand, type VoiceCommand } from "./voiceCommands";
import { executeSystemAction } from "./systemActions";
import { executeBrowserControl } from "./browserControl";
import { recordControl, type ControlPayload } from "./controlSecurity";
import type { ControlReply } from "./controlTypes";
import { voiceControl, type VoiceControl } from './voiceControl';
import { interruptResponses } from './responseCancellation';

type Pending = { approval: NonNullable<ControlReply['approval']>; steps: VoiceCommand[]; timer?: ReturnType<typeof setTimeout> };
type VoiceState = { enabled: boolean; status: string; level?: number; levelAt?: number; peak?: number; signal?: string; child?: ChildProcessWithoutNullStreams; timer?: ReturnType<typeof setTimeout>; retries: number; queue: string[]; busy: boolean; generation: number; pending?: Pending; events: Array<{ time: string; text: string }>; last: string; lastTime: number };
type CommandResult = { id: number; action: string; label: string; data: ControlReply };
const resultGlobal = globalThis as typeof globalThis & { ultronVoiceResult?: CommandResult; ultronVoiceContext?: { snapshot?: string; deviceId?: string; capability?: string; planning?: AbortController } };
const context = resultGlobal.ultronVoiceContext ??= {};
const shared = globalThis as typeof globalThis & { ultronDesktopVoice?: VoiceState };
const state = shared.ultronDesktopVoice ??= { enabled: false, status: "Desktop voice off", retries: 0, queue: [], busy: false, generation: 0, events: [], last: "", lastTime: 0 };
const preference = path.join(process.cwd(), '.ultron', 'desktop-voice.json');
const listenersGlobal = globalThis as typeof globalThis & { ultronVoiceListeners?: Set<(kind: VoiceControl) => void> };
const listeners = listenersGlobal.ultronVoiceListeners ??= new Set<(kind: VoiceControl) => void>();
let preferenceWrite: Promise<void> = Promise.resolve();
function broadcast(kind: VoiceControl) { for (const listener of listeners) { try { listener(kind); } catch { listeners.delete(listener); } } }

export function desktopVoiceEvents(signal: AbortSignal) {
  // Notify open assistant pages immediately when voice stops a reply or disables listening.
  if (listeners.size >= 8) throw new Error('Too many desktop voice connections. Close unused assistant tabs.');
  const encoder = new TextEncoder();
  let cleanup = () => {};
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      const send = (kind: VoiceControl) => { controller.enqueue(encoder.encode(JSON.stringify({ kind }) + '\n')); };
      const heartbeat = setInterval(() => controller.enqueue(encoder.encode('\n')), 15000);
      cleanup = () => { clearInterval(heartbeat); listeners.delete(send); signal.removeEventListener('abort', close); };
      const close = () => { cleanup(); try { controller.close(); } catch {} };
      listeners.add(send);
      signal.addEventListener('abort', close, { once: true });
      if (signal.aborted) close();
      else controller.enqueue(encoder.encode('\n'));
    },
    cancel() { cleanup(); },
  });
  return new Response(body, { headers: { 'Content-Type': 'application/x-ndjson', 'Cache-Control': 'no-store, no-transform' } });
}

export function interruptDesktopVoice() {
  context.planning?.abort();
  state.generation++; state.queue = []; clearTimeout(state.pending?.timer); state.pending = undefined;
  state.child?.stdin.write('interrupt\n');
  state.status = state.enabled ? 'Interrupted · listening for Ultron' : 'Desktop voice off';
  interruptResponses();
  broadcast('interrupt');
  void import('./agentRunner').then(({ pauseActiveTasks }) => pauseActiveTasks()).catch(() => log('Could not pause agent tasks. Review their status before continuing.'));
  log('Interrupted current response and queued steps. Already-started external actions may finish.');
}
function log(text: string) { state.events = [{ time: new Date().toISOString(), text: text.slice(0, 800) }, ...state.events].slice(0, 30); }
function feedback(text: string) {
  log(text);
  if (state.child?.stdin.writable) state.child.stdin.write(`say:${text.replace(/[\r\n]/g, ' ').slice(0, 350)}\n`);
}
export function desktopVoiceState() {
  if (state.pending && state.pending.approval.expires <= Date.now()) { state.pending = undefined; log("Approval expired; remaining steps discarded."); }
  const latest = resultGlobal.ultronVoiceResult;
  return { enabled: state.enabled, status: state.status, busy: state.busy, queued: state.queue.length, approval: state.pending?.approval, events: state.events, capability: context.capability, result: latest ? { id: latest.id, action: latest.action, label: latest.label } : undefined, audio: { level: state.level ?? null, peak: state.peak ?? null, updatedAt: state.levelAt ?? null, signal: state.signal || 'No reported signal problem', stale: !state.levelAt || Date.now() - state.levelAt > 5000 } };
}
export function desktopVoiceResult(id: number) {
  // Send screenshots and long results only when requested, not with every microphone status update.
  const result = resultGlobal.ultronVoiceResult;
  if (!result || result.id !== id) throw new Error('This voice result was replaced. Refresh the voice panel and view the latest result.');
  return result;
}
function retainResult(action: string, label: string, data: ControlReply) {
  resultGlobal.ultronVoiceResult = { id: (resultGlobal.ultronVoiceResult?.id || 0) + 1, action, label, data };
}
async function execute(steps: VoiceCommand[], generation: number) {
  // Run commands in order and pause the remaining work whenever permission is needed.
  let spokenResult = '';
  for (let index = 0; index < steps.length; index++) {
    if (!state.enabled || generation !== state.generation) return;
    const payload = { ...steps[index].payload } as ControlPayload;
    if (payload.action.startsWith('browser') && !['browserOpen', 'browserNewTab', 'browserInspect', 'browserClose'].includes(payload.action) && !payload.snapshot) payload.snapshot = context.snapshot || '';
    if (payload.action.startsWith('adb') && !payload.deviceId && context.deviceId) payload.deviceId = context.deviceId;
    const reply = await (payload.action.startsWith('browser') ? executeBrowserControl(payload, 'http://127.0.0.1:3000') : executeSystemAction(payload)) as ControlReply;
    if (generation !== state.generation) return;
    if (reply.browser) context.snapshot = reply.browser.snapshot;
    if (reply.browserClosed) context.snapshot = undefined;
    if (reply.deviceId) { context.deviceId = reply.deviceId; payload.deviceId = reply.deviceId; }
    if (reply.approval) {
      const pending: Pending = { approval: reply.approval, steps: [{ ...steps[index], payload }, ...steps.slice(index + 1)] };
      state.pending = pending;
      pending.timer = setTimeout(() => {
        if (state.pending !== pending) return;
        state.pending = undefined; state.queue = [];
        feedback('Approval expired. Remaining commands discarded. Listening for your next wake-word command.');
      }, Math.max(0, reply.approval.expires - Date.now()));
      feedback('Approval required in the ULTRON desktop voice panel. Remaining steps are paused.'); return;
    }
    if (!reply.ok) throw new Error(reply.error || 'Action failed');
    retainResult(payload.action, steps[index].label, reply);
    recordControl(payload.action, true, 'Desktop voice action returned success.');
    log(reply.message || steps[index].label);
    if (reply.browser) log(`Current browser elements: ${reply.browser.elements.map(element => `${element.id}: ${element.label}`).join('; ').slice(0, 650) || 'none'}. Use the latest result panel for full details.`);
    if (reply.results || reply.text || reply.summary || reply.devices || reply.dataUrl) spokenResult = (reply.summary || reply.message || 'Result ready.').slice(0, 250) + ' View the latest result in the desktop voice panel.';
  }
  feedback(`${spokenResult || 'Command completed.'} Ready for your next wake-word command.`);
}
async function drain() {
  if (state.busy || state.pending || !state.enabled) return;
  state.busy = true;
  const generation = state.generation;
  try {
    while (state.queue.length && !state.pending && state.enabled && generation === state.generation) {
      const command = state.queue.shift()!;
      if (isAgentCommand(command)) {
        // A spoken research goal creates a plan for review; it does not start its tools.
        const controller = new AbortController(); context.planning = controller;
        try {
          const { createTask } = await import('./agentRunner');
          if (generation !== state.generation || !state.enabled) continue;
          const task = await createTask(command, undefined, controller.signal);
          if (generation !== state.generation || !state.enabled) continue;
          retainResult('agentPlan', task.title, { ok: true, message: `Created plan: ${task.title}. Review it in Agent tasks. No tool has run.`, text: JSON.stringify({ id: task.id, questions: task.questions, steps: task.steps.map(step => ({ title: step.title, tool: step.tool, args: step.args })) }, null, 2) });
          feedback(`Plan created: ${task.title}. Review it in Agent tasks. No actions have run.`);
        } catch (error) { if (generation === state.generation) feedback(`Planning stopped: ${error instanceof Error ? error.message : 'provider unavailable'}`); }
        finally { if (context.planning === controller) context.planning = undefined; }
        continue;
      }
      const plan = parseVoicePlan(command);
      if (!plan) { feedback('That desktop command is not supported. No action was performed. Use the assistant for general questions.'); continue; }
      try { await execute(plan, generation); }
      catch (error) { if (generation === state.generation) feedback(`Action stopped: ${error instanceof Error ? error.message : 'unknown error'}. Later steps were not run.`); }
    }
  } finally { state.busy = false; if (state.queue.length && state.enabled && !state.pending) void drain(); }
}
function accept(text: string) {
  desktopVoiceState();
  if (!/^\s*(?:hey\s+)?ultron\b/i.test(text)) return;
  const control = voiceControl(text);
  if (control === 'stopListening') { void setDesktopVoice(false).catch(() => { state.status = 'Microphone stopped, but saving the disabled preference failed. Retry Disable in the interface.'; }); return; }
  if (control === 'interrupt') { interruptDesktopVoice(); return; }
  const command = normalizeCommand(text);
  if (!command) { feedback('Ready. Say Ultron followed by your command.'); return; }
  if (/^(yes|confirm|approve)\b/i.test(command)) { feedback('Use the on-screen approval button. Voice cannot approve sensitive actions.'); return; }
  if (state.pending) { feedback('Resolve the pending approval first, or say Ultron cancel.'); return; }
  if (command === state.last && Date.now() - state.lastTime < 2500) return;
  if (state.queue.length >= 5) { feedback('Command queue full. Please wait.'); return; }
  state.last = command; state.lastTime = Date.now(); state.queue.push(command); log(`Heard: ${command}`); void drain();
}
function startWorker() {
  // Keep Windows speech in its own process so it can listen while another app is in front.
  if (!state.enabled || state.child) return;
  state.level = undefined; state.levelAt = undefined; state.peak = undefined; state.signal = undefined;
  state.status = 'Starting Windows microphone';
  const child = spawn(path.join(process.env.SystemRoot || 'C:\\Windows', 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe'), ['-NoProfile', '-NonInteractive', '-File', path.join(process.cwd(), 'scripts', 'voice-listener.ps1')], { windowsHide: true, stdio: 'pipe' });
  state.child = child;
  child.stdin.on('error', () => {});
  let ready = false;
  const startedAt = Date.now();
  const startup = setTimeout(() => { if (!ready && state.child === child) { state.status = 'Speech engine startup timed out'; child.kill(); } }, 20000);
  createInterface({ input: child.stdout }).on('line', line => {
    if (state.child !== child || !state.enabled) return;
    try {
      const event = JSON.parse(line);
      if (event.type === 'ready') { ready = true; clearTimeout(startup); state.status = event.text; }
      else if (event.type === 'listening') state.status = event.text;
      else if (event.type === 'level') {
        const level = Number(event.text);
        if (Number.isFinite(level) && level >= 0 && level <= 100) { state.level = level; state.levelAt = Date.now(); state.peak = Math.max(state.peak || 0, level); }
      }
      else if (event.type === 'signal') state.signal = String(event.text).slice(0, 100);
      else if (event.type === 'capabilities') context.capability = String(event.text).slice(0, 400);
      else if (event.type === 'rejected') { state.status = String(event.text).slice(0, 500); log(state.status); }
      else if (event.type === 'command' && typeof event.text === 'string' && event.text.length <= 1000) accept(event.text);
      else if (event.type === 'fatal' || event.type === 'ended') { state.status = String(event.text).slice(0, 300); log(state.status); child.kill(); }
    } catch { state.status = 'Speech engine returned an invalid event'; }
  });
  child.stderr.on('data', () => { state.status = 'Windows speech engine reported an error. Check the language pack and microphone permissions.'; });
  child.once('error', error => { state.status = `Cannot start speech engine: ${error.message}`; });
  child.once('close', () => {
    clearTimeout(startup);
    if (state.child !== child) return;
    state.child = undefined;
    if (!state.enabled) return;
    if (ready && Date.now() - startedAt > 60000) state.retries = 0;
    if (++state.retries > 5) { state.status = 'Desktop voice stopped after repeated failures. Check Windows microphone access and English speech pack, then turn off/on to retry.'; return; }
    state.status += ' · reconnecting';
    state.timer = setTimeout(startWorker, Math.min(30000, 1000 * 2 ** state.retries));
  });
}
export async function setDesktopVoice(enabled: boolean) {
  if (enabled && process.platform !== 'win32') throw new Error('Desktop voice currently requires Windows. Use browser hands-free on other systems.');
  state.enabled = enabled;
  if (enabled) { clearTimeout(state.timer); state.retries = 0; startWorker(); }
  else {
    // Disabling voice releases the microphone and prevents automatic reconnection.
    context.planning?.abort();
    state.generation++; state.queue = []; clearTimeout(state.pending?.timer); state.pending = undefined; clearTimeout(state.timer);
    const child = state.child; state.child = undefined;
    child?.stdin.end('stop\n'); if (child) setTimeout(() => { if (child.exitCode === null) child.kill(); }, 1500);
    state.status = 'Desktop voice off · microphone released';
    broadcast('stopListening');
  }
  preferenceWrite = preferenceWrite.catch(() => {}).then(async () => {
    await fs.mkdir(path.dirname(preference), { recursive: true });
    await fs.writeFile(preference, JSON.stringify({ enabled: state.enabled }));
  });
  await preferenceWrite;
  return desktopVoiceState();
}
export async function restoreDesktopVoice() {
  try { if (JSON.parse(await fs.readFile(preference, 'utf8')).enabled === true) { state.enabled = true; startWorker(); } } catch { return; }
}
export async function approveDesktopVoice(token: string) {
  const pending = state.pending;
  if (!pending || pending.approval.token !== token || pending.approval.expires <= Date.now() || state.busy) throw new Error('Approval expired, changed, or another command is still running.');
  clearTimeout(pending.timer); state.pending = undefined; state.busy = true;
  const steps = pending.steps.map((step, index) => index ? step : { ...step, payload: { ...step.payload, approvalToken: token } });
  try { await execute(steps, state.generation); } catch (error) { feedback(`Action failed: ${error instanceof Error ? error.message : 'unknown error'}`); }
  finally { state.busy = false; void drain(); }
  return desktopVoiceState();
}
export function declineDesktopVoice() { clearTimeout(state.pending?.timer); state.pending = undefined; state.queue = []; feedback('Cancelled. No remaining steps will run.'); return desktopVoiceState(); }
