import { execFile, spawn } from 'node:child_process';
import path from 'node:path';
import { promisify } from 'node:util';
import { chromium, type BrowserContext, type Page } from 'playwright-core';
import { APP_CATALOG, appIdForAlias } from './appCatalog';
import { approvalFor, type ControlPayload } from './controlSecurity';

const exec = promisify(execFile);
const projectDirectory = process.cwd();
const windowsDirectory = process.env.SystemRoot || 'C:\\Windows';
const powerShell = path.join(windowsDirectory, 'System32/WindowsPowerShell/v1.0/powershell.exe');
const inventoryScript = path.join(projectDirectory, 'scripts/app-inventory.ps1');
interface ProcessIdentity { pid: number; name: string; started: string; visible: boolean; }
export interface AppInventory { apps: Array<{ Name: string; AppID: string }>; paths?: Record<string, string>; processes: ProcessIdentity[]; startCoverage: string; defaultBrowser: string; }
interface AppState { context?: BrowserContext; opening?: Promise<BrowserContext>; pages: Map<string, Page>; lastRoute: Map<string, string>; busy: Set<string>; }
const shared = globalThis as typeof globalThis & { ultronSmartApps?: AppState };
const state: AppState = shared.ultronSmartApps ??= { pages: new Map(), lastRoute: new Map(), busy: new Set() };

async function launch(binary: string, args: string[]) {
  await new Promise<void>((resolve, reject) => {
    const child = spawn(binary, args, { detached: true, windowsHide: false, stdio: 'ignore' });
    child.once('error', reject); child.once('spawn', () => { child.unref(); resolve(); });
  });
}

export async function getAppInventory(): Promise<AppInventory> {
  const { stdout } = await exec(powerShell, ['-NoProfile', '-NonInteractive', '-File', inventoryScript], { windowsHide: true, timeout: 20000, maxBuffer: 2 * 1024 * 1024 });
  return JSON.parse(stdout.trim().replace(/^\uFEFF/, ''));
}

export async function resolveInstalledApp(id: string, inventory: AppInventory) {
  // Prefer a known installed application before considering its official website.
  const app = APP_CATALOG[id];
  if (!app) throw new Error('Application is not supported.');
  const registryNames: Record<string, string> = { vscode: 'Code.exe', spotify: 'Spotify.exe', telegram: 'Telegram.exe', whatsapp: 'WhatsApp.exe' };
  const registeredPath = inventory.paths?.[app.executable || registryNames[id]];
  if (registeredPath && path.isAbsolute(registeredPath) && !/[\r\n"\x00]/.test(registeredPath)) return { kind: 'executable' as const, value: registeredPath };
  const registered = inventory.apps.find(entry => app.startNames.some(name => name.toLowerCase() === entry.Name.toLowerCase()));
  if (registered && registered.AppID && !/[\r\n"\x00]/.test(registered.AppID)) return { kind: 'registered' as const, value: registered.AppID };
  if (inventory.startCoverage !== 'available') throw new Error('Windows app inventory is unavailable. Installation status is unknown; no automatic fallback was opened.');
  return null;
}

async function appContext(channel: string) {
  // Use a separate browser profile so ULTRON does not take over the user's personal tabs.
  if (state.context) return state.context;
  if (!state.opening) {
    state.opening = chromium.launchPersistentContext(path.join(projectDirectory, '.ultron/app-browser-profile'), {
      channel, headless: false, acceptDownloads: false, viewport: null,
    }).then(context => {
      state.context = context;
      context.on('close', () => { if (state.context === context) { state.context = undefined; state.pages.clear(); } });
      return context;
    }).finally(() => { state.opening = undefined; });
  }
  return state.opening;
}

async function openWeb(id: string, inventory: AppInventory) {
  const app = APP_CATALOG[id];
  if (!app.web) throw new Error(`${app.name} was not found in registered apps or supported installation locations, and has no web fallback.`);
  if (!['chrome', 'msedge'].includes(inventory.defaultBrowser)) {
    await launch(path.join(windowsDirectory, 'explorer.exe'), [app.web]);
    state.lastRoute.set(id, 'unmanaged');
    return { ok: true, installed: false, route: 'default-browser', message: `${app.name} was not found. Windows accepted its official website in your default browser. This browser is not controllable by ULTRON; close that tab manually. No unrelated tabs will be closed.` };
  }
  const context = await appContext(inventory.defaultBrowser);
  let page = state.pages.get(id);
  if (page && !page.isClosed()) {
    await page.bringToFront();
  } else {
    page = await context.newPage();
    state.pages.set(id, page);
    const ownedPage = page;
    page.on('close', () => { if (state.pages.get(id) === ownedPage) state.pages.delete(id); });
    try { await page.goto(app.web, { waitUntil: 'domcontentloaded', timeout: 25000 }); }
    catch {
      throw new Error(`${app.name}'s owned tab opened, but navigation was not confirmed. Check its network/error page. No desktop launch was claimed.`);
    }
  }
  state.lastRoute.set(id, 'web');
  return { ok: true, installed: false, route: 'owned-web', message: `Opened ${app.name} Web using your default ${inventory.defaultBrowser === 'chrome' ? 'Chrome' : 'Edge'} in ULTRON's separate profile. This app's tab can be closed independently. Your normal browser profile is untouched; signing in may be required.` };
}

export async function executeSmartApp(payload: ControlPayload) {
  // App availability, launch and close requests all use the same supported application catalog.
  if (process.platform !== 'win32') throw new Error('Smart desktop application resolution currently requires Windows.');
  const id = appIdForAlias(String(payload.app || ''));
  if (!id) throw new Error('Application is not allowlisted. Arbitrary program names and shell commands are not permitted.');
  if (state.busy.has(id)) throw new Error('Another action for this application is still running. Please wait.');
  state.busy.add(id);
  try {
    const app = APP_CATALOG[id];
    const inventory = await getAppInventory();
    const installed = await resolveInstalledApp(id, inventory);
    if (payload.action === 'appAvailability') return { ok: true, app: id, installed: !!installed, detection: installed?.kind || 'not-found-in-supported-inventory', webFallback: app.web || null, defaultBrowser: inventory.defaultBrowser };
    if (payload.action === 'openApp') {
      if (!installed) return await openWeb(id, inventory);
      if (installed.kind === 'registered') await launch(path.join(windowsDirectory, 'explorer.exe'), [`shell:AppsFolder\\${installed.value}`]);
      else await launch(installed.value, []);
      state.lastRoute.set(id, 'desktop');
      return { ok: true, installed: true, route: 'desktop', message: `Found installed ${app.name}; Windows accepted its desktop launch request. No website fallback was opened.` };
    }
    if (payload.action !== 'closeApp') throw new Error('Unsupported application action.');
    const page = state.pages.get(id);
    const ownedWeb = page && !page.isClosed() ? page : undefined;
    const processes = inventory.processes.filter(item => item.visible && app.processes.some(name => name.toLowerCase() === item.name.toLowerCase()));
    if (ownedWeb && (state.lastRoute.get(id) === 'web' || !processes.length)) {
      if (!app.web || new URL(ownedWeb.url()).hostname !== new URL(app.web).hostname) return { ok: false, error: `The owned ${app.name} tab is now on another host (possibly sign-in or another site). Close it manually; ULTRON will not close a different site's tab.` };
      const approval = approvalFor({ action: 'closeApp', app: id, route: 'owned-web', url: ownedWeb.url(), approvalToken: payload.approvalToken }, `Close only ULTRON's ${app.name} tab (${ownedWeb.url()}). Unsaved edits in that tab could be lost. Other tabs and desktop apps stay open.`);
      if (approval) return { ok: false, approval };
      await ownedWeb.close({ runBeforeUnload: true });
      return { ok: true, message: `${app.name} tab close requested. If the page asks about unsaved work, finish that prompt in the browser. Other tabs were not closed.` };
    }
    if (processes.length) {
      if (processes.length > 40) throw new Error('Too many matching windows; close them manually.');
      const targets = processes.map(({ pid, name, started }) => ({ pid, name, started })).sort((first, second) => first.pid - second.pid);
      const approval = approvalFor({ action: 'closeApp', app: id, route: 'desktop', targets: JSON.stringify(targets), approvalToken: payload.approvalToken }, `Request ${processes.length} ${app.name} window process(es) to close. Save your work first. No force-kill; no unrelated app or browser will be terminated.`);
      if (approval) return { ok: false, approval };
      const { stdout } = await exec(powerShell, ['-NoProfile', '-NonInteractive', '-File', inventoryScript, '-CloseTargets', JSON.stringify(targets)], { windowsHide: true, timeout: 15000, maxBuffer: 65536 });
      return { ok: true, message: `Windows close requests for ${app.name}: ${stdout.trim()}. Unsaved-work dialogs may require your attention.`, route: 'desktop' };
    }
    if (state.lastRoute.get(id) === 'unmanaged') return { ok: false, error: `${app.name} was opened in an untracked default-browser tab. Close it manually; ULTRON will not terminate your whole browser.` };
    return { ok: false, error: `No closable ${app.name} desktop window or ULTRON-owned tab was found. ${installed ? 'A registered app may use an unsupported host process or PWA window; it was not force-closed.' : 'The desktop app was not found.'} Unrelated applications were left untouched.` };
  } finally { state.busy.delete(id); }
}
