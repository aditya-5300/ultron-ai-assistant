import { execFile, spawn } from "node:child_process";
import { promises as fs, type Dirent } from "node:fs";
import os from "node:os";
import path from "node:path";
import { promisify } from "node:util";
import { approvalFor, controlEvents, type ControlPayload } from "./controlSecurity";
import { DOCUMENT_LIMIT, DOCUMENT_EXTENSIONS, extractDocument } from "./documents";
import { executeSmartApp } from './smartApps';
import { APP_CATALOG } from './appCatalog';

const exec = promisify(execFile);
const project = /* turbopackIgnore: true */ process.cwd();
const home = /* turbopackIgnore: true */ os.homedir();
const roots: Record<string, string> = { project, desktop: path.join(home, "Desktop"), documents: path.join(home, "Documents"), downloads: path.join(home, "Downloads") };
const privateName = /^(\.|id_(rsa|ed25519)|credentials|secrets)|\.(pem|key|pfx|p12)$/i;

export async function permittedFile(candidate: string) {
  // Resolve the real file location before checking access, including folders that link elsewhere.
  const canonical = await fs.realpath(candidate);
  const allowed = (await Promise.all(Object.values(roots).map(root => fs.realpath(root).catch(() => "")))).filter(Boolean);
  if (!allowed.some(root => { const relative = path.relative(root, canonical); return !relative.startsWith("..") && !path.isAbsolute(relative); })) throw new Error("Path is outside the permitted project, Desktop, Documents and Downloads folders.");
  if (canonical.split(path.sep).some(part => privateName.test(part))) throw new Error("Hidden files, credentials and key files are private.");
  return canonical;
}

async function launch(command: string, args: string[]) {
  await new Promise<void>((resolve, reject) => {
    const child = spawn(command, args, { detached: true, stdio: "ignore", windowsHide: false });
    child.once("error", reject);
    child.once("spawn", () => { child.unref(); resolve(); });
  });
}

async function findFiles(rootName: string, query: string) {
  // Limit the search so a request does not walk the entire computer or freeze the assistant.
  if (!roots[rootName]) throw new Error("Choose project, desktop, documents or downloads.");
  const root = await permittedFile(roots[rootName]);
  let scanned = 0;
  const results: Array<{ name: string; path: string; size: number }> = [];
  async function visit(directory: string, depth: number): Promise<void> {
    if (depth > 6 || scanned >= 2500 || results.length >= 24) return;
    const entries: Dirent[] = await fs.readdir(directory, { withFileTypes: true }).catch(() => []);
    for (const entry of entries) {
      if (scanned >= 2500 || results.length >= 24) return;
      if (entry.isSymbolicLink() || privateName.test(entry.name) || ["node_modules", ".next"].includes(entry.name)) continue;
      scanned++;
      const candidate = path.join(directory, entry.name);
      if (entry.isDirectory()) await visit(candidate, depth + 1);
      else if (entry.isFile() && entry.name.toLowerCase().includes(query.toLowerCase())) {
        try { const safe = await permittedFile(candidate); results.push({ name: entry.name, path: safe, size: (await fs.stat(safe)).size }); }
        catch { continue; }
      }
    }
  }
  await visit(root, 0);
  return { ok: true, root, scanned, results, limited: scanned >= 2500 || results.length >= 24, message: `Found ${results.length} file(s) in ${rootName}. Search is limited to 2,500 entries and 24 results.` };
}

async function adb(args: string[], binary = false) {
  try {
    return await exec(process.env.ADB_PATH || "adb", args, { encoding: binary ? "buffer" : "utf8", timeout: 20000, windowsHide: true, maxBuffer: 8 * 1024 * 1024 });
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") throw new Error("ADB is not installed or on the server PATH. Install Android platform-tools, or set ADB_PATH to adb.exe, then restart the app.");
    throw new Error(`ADB failed: ${error instanceof Error ? error.message.slice(0, 500) : "unknown error"}`);
  }
}

async function devices() {
  const { stdout } = await adb(["devices", "-l"]);
  return String(stdout).split(/\r?\n/).slice(1).map(line => line.trim()).filter(Boolean).map(line => { const [id, state, ...details] = line.split(/\s+/); return { id, state, details: details.join(" ") }; });
}

async function android(payload: ControlPayload) {
  // Use only an authorized phone and show permission requests before changing its screen or input.
  const connected = await devices();
  if (payload.action === "adbDevices") return { ok: true, devices: connected, output: connected.length ? connected.map(device => `${device.id}: ${device.state} ${device.details}`).join("\n") : "No phone connected. Connect USB, enable USB debugging and accept the RSA prompt on the phone." };
  const authorized = connected.filter(device => device.state === "device");
  const selected = payload.deviceId ? authorized.find(device => device.id === payload.deviceId) : authorized.length === 1 ? authorized[0] : undefined;
  if (!selected) throw new Error(authorized.length > 1 ? "Multiple phones connected. Select a device ID in Tools." : "No authorized phone available. Connect USB and accept the debugging prompt; check for unauthorized/offline devices.");
  const prefix = ["-s", selected.id];
  if (payload.action === "adbSelectDevice") return { ok: true, deviceId: selected.id, message: `Selected phone ${selected.id}.` };
  if (payload.action === "adbScreenshot") {
    const { stdout } = await adb([...prefix, "exec-out", "screencap", "-p"], true);
    const buffer = Buffer.isBuffer(stdout) ? stdout : Buffer.from(stdout);
    if (!buffer.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]))) throw new Error("Phone did not return a PNG screenshot.");
    return { ok: true, deviceId: selected.id, dataUrl: `data:image/png;base64,${buffer.toString("base64")}`, message: "Phone screenshot captured." };
  }
  const { stdout: sizeOutput } = await adb([...prefix, "shell", "wm", "size"]);
  const sizes = [...String(sizeOutput).matchAll(/(\d+)x(\d+)/g)];
  if (!sizes.length) throw new Error("Could not read phone screen dimensions.");
  const screenWidth = Number(sizes.at(-1)![1]), screenHeight = Number(sizes.at(-1)![2]);
  const coordinate = (value: unknown, maximum: number) => { if (typeof value !== "number" || !Number.isInteger(value) || value < 0 || value >= maximum) throw new Error(`Coordinates must be integers inside the ${screenWidth} × ${screenHeight} screen.`); return String(value); };
  let args: string[];
  if (payload.action === "adbOpenApp") {
    if (typeof payload.packageName !== "string" || !/^[a-zA-Z][a-zA-Z0-9_]*(\.[a-zA-Z0-9_]+)+$/.test(payload.packageName)) throw new Error("Use a valid Android package name, such as com.android.settings.");
    args = ["shell", "monkey", "-p", payload.packageName, "-c", "android.intent.category.LAUNCHER", "1"];
  } else if (payload.action === "adbTap") args = ["shell", "input", "tap", coordinate(payload.x, screenWidth), coordinate(payload.y, screenHeight)];
  else if (payload.action === "adbSwipe") {
    let { x1, y1, x2, y2 } = payload;
    if (payload.direction) {
      const positions: Record<string, number[]> = { up: [.5, .8, .5, .2], down: [.5, .2, .5, .8], left: [.8, .5, .2, .5], right: [.2, .5, .8, .5] };
      const values = positions[String(payload.direction)];
      if (!values) throw new Error("Swipe direction must be up, down, left or right.");
      [x1, y1, x2, y2] = values.map((value, index) => Math.round(value * (index % 2 ? screenHeight : screenWidth)));
    }
    args = ["shell", "input", "swipe", coordinate(x1, screenWidth), coordinate(y1, screenHeight), coordinate(x2, screenWidth), coordinate(y2, screenHeight), String(Math.max(100, Math.min(5000, Number(payload.durationMs) || 300)))];
  } else if (payload.action === "adbType") {
    const text = String(payload.text || "");
    if (!text.trim() || text.length > 500 || !/^[a-zA-Z0-9 .,!?@_-]+$/.test(text)) throw new Error("Phone typing supports up to 500 English letters, numbers, spaces and basic punctuation. Shell symbols and passwords are not supported.");
    args = ["shell", "input", "text", `'${text.replace(/ /g, "%s")}'`];
  } else if (payload.action === "adbKey") {
    const keys: Record<string, string> = { HOME: "3", BACK: "4", ENTER: "66", POWER: "26" };
    if (!keys[String(payload.key)]) throw new Error("Supported phone keys: HOME, BACK, ENTER, POWER.");
    args = ["shell", "input", "keyevent", keys[String(payload.key)]];
  } else throw new Error("Unsupported Android action.");
  const approval = approvalFor({ ...payload, deviceId: selected.id }, `${payload.action} on phone ${selected.id}: ${args.slice(2).join(" ")}. This changes the phone's current screen or input.`);
  if (approval) return { ok: false, approval, deviceId: selected.id };
  const { stdout, stderr } = await adb([...prefix, ...args]);
  const output = `${stdout || ""}${stderr || ""}`.trim();
  if (payload.action === "adbOpenApp" && /no activities|aborted|does not exist|not found|error:/i.test(output)) throw new Error(output || "Phone app launch failed.");
  return { ok: true, deviceId: selected.id, output: output || `${payload.action} completed on ${selected.id}.`, screen: { width: screenWidth, height: screenHeight } };
}

export async function executeSystemAction(payload: ControlPayload) {
  // Each supported action has its own checks; unknown actions never become arbitrary shell commands.
  if (payload.action === "commandHelp") return { ok: true, message: "Supported command examples. Approvals must be clicked, not spoken.", text: [
    "ULTRON, Stop. — interrupt without disabling listening",
    "ULTRON, Stop Listening. — release the voice microphone",
    `Open or close: ${Object.values(APP_CATALOG).map(app => app.name).join(", ")}. Closing is limited to supported windows or owned tabs and requires approval.`,
    "Open new tab; browser open YouTube; browser inspect; browser click 1; browser fill 2 with hello; browser scroll down; browser back; browser close.",
    "Open Documents / Downloads / Desktop / project; open D drive; find files named report in documents; open file <full path>; summarize file <full path>.",
    "List phone devices; phone screenshot; phone open YouTube; phone home; phone back; phone swipe up; phone tap 300 500; phone type hello. Requires authorized ADB.",
    "Run a Defender quick scan. Requires approval; does not guarantee a malware-free computer.",
    "Research SSD versus HDD and make a cited report. Creates a plan for review; no tools run automatically.",
    "Chain up to six supported actions with then. For fills and phone typing, the rest of the phrase is literal text.",
    "Desktop free-text recognition depends on the installed English dictation engine. Exact URLs and paths are more reliable when typed."
  ].join("\n\n") };
  if (payload.action === 'securityScan') {
    if (process.platform !== 'win32') throw new Error('Microsoft Defender scanning requires Windows.');
    const approval = approvalFor(payload, 'Run a Microsoft Defender quick scan. It can use CPU and battery; Defender may quarantine detected threats according to your existing policy. No security settings will be changed.');
    if (approval) return { ok: false, approval };
    try {
      await exec(path.join(process.env.SystemRoot || 'C:\\Windows', 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe'), ['-NoProfile', '-NonInteractive', '-Command', 'Start-MpScan -ScanType QuickScan -ErrorAction Stop'], { windowsHide: true, timeout: 120000, maxBuffer: 65536 });
      return { ok: true, message: 'Defender quick-scan command completed. Review Windows Security Protection history for findings; this is not a malware-free guarantee.' };
    } catch {
      throw new Error('Defender did not confirm completion within two minutes. Check Windows Security for scan status, availability and permissions; a started scan may still be running. No elevation or automatic retry was attempted.');
    }
  }
  if (payload.action === "controlStatus") return { ok: true, apps: Object.keys(APP_CATALOG), roots: Object.keys(roots), events: controlEvents(), message: "Local controller online. No phone or browser session is accessed by this check." };
  if (payload.action.startsWith("adb")) return android(payload);
  if (['openApp', 'closeApp', 'appAvailability'].includes(payload.action)) return executeSmartApp(payload);
  if (payload.action === 'openDrive') {
    if (process.platform !== 'win32') throw new Error('Drive-letter navigation requires Windows.');
    if (typeof payload.drive !== 'string' || !/^[a-z]$/i.test(payload.drive)) throw new Error('Choose one drive letter, such as D. Paths and shell arguments are not accepted.');
    const root = `${payload.drive.toUpperCase()}:\\`;
    try { await fs.access(root); } catch { throw new Error(`Drive ${root} is unavailable, disconnected or not accessible. No window was opened.`); }
    await launch(path.join(process.env.SystemRoot || 'C:\\Windows', 'explorer.exe'), [root]);
    return { ok: true, message: `Windows accepted the request to open ${root} in File Explorer.` };
  }
  if (payload.action === "openWebsite") {
    const url = new URL(String(payload.url || ""));
    if (!["https:", "http:"].includes(url.protocol) || url.username || url.password) throw new Error("Only HTTP(S) URLs without embedded credentials are allowed.");
    await launch("explorer.exe", [url.href]);
    return { ok: true, message: `Windows accepted the request to open ${url.hostname}. For clicks and form filling, use the managed Browser controls.` };
  }
  if (payload.action === "openFolder") {
    const folder = roots[String(payload.root)];
    if (!folder) throw new Error("Choose project, desktop, documents or downloads.");
    await fs.access(folder);
    await launch("explorer.exe", [folder]);
    return { ok: true, message: `Windows accepted the request to open ${payload.root}.` };
  }
  if (payload.action === "searchFiles") {
    if (typeof payload.query !== "string" || !payload.query.trim()) throw new Error("A filename search query is required.");
    return findFiles(String(payload.root || "documents"), payload.query.trim());
  }
  if (["summarizeFile", "openFile"].includes(payload.action)) {
    if (typeof payload.filePath !== "string") throw new Error("Choose a file path first.");
    const safe = await permittedFile(payload.filePath);
    const stat = await fs.stat(safe);
    if (!stat.isFile() || stat.size > DOCUMENT_LIMIT || !DOCUMENT_EXTENSIONS.has(path.extname(safe).toLowerCase())) throw new Error("Choose a supported PDF, DOCX, text or code document smaller than 5 MB.");
    if (payload.action === "openFile") {
      const extension = path.extname(safe).toLowerCase();
      await launch([".pdf", ".docx"].includes(extension) ? "explorer.exe" : "notepad.exe", [safe]);
      return { ok: true, message: `Windows accepted the request to open ${path.basename(safe)}.` };
    }
    return { ok: true, filePath: safe, ...await extractDocument(await fs.readFile(safe), safe) };
  }
  throw new Error("Unsupported system action. Arbitrary shell commands are not permitted.");
}
