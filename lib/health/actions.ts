import { execFile, spawn } from "node:child_process";
import { promisify } from "node:util";
import path from "node:path";
import { approvalFor } from "../controlSecurity";
import { createStore } from "./core.cjs";

const exec = promisify(execFile);
const windows = process.env.SystemRoot || "C:\\Windows";
const balanced = "381b4222-f694-41f0-9685-ff5bb260df2e";
const powercfg = path.join(windows, "System32", "powercfg.exe");
export const HEALTH_ACTIONS: Record<string, { title: string; detail: string }> = {
  setBalanced: { title: "Switch to Balanced", detail: "Change the active Windows power plan to Balanced. Peak performance can change. The previous plan is saved for an approved restore. No apps are closed." },
  restorePowerPlan: { title: "Restore previous power plan", detail: "Restore the plan saved before ULTRON's last Balanced change. This changes performance/power behavior again." },
  openTaskManager: { title: "Open Task Manager", detail: "Open Windows Task Manager for manual investigation. ULTRON will not terminate any process." },
  openPowerSettings: { title: "Open power settings", detail: "Open Windows Power & battery settings. No setting is changed automatically." },
  openStorageSettings: { title: "Open storage settings", detail: "Open Windows Storage settings. No file is deleted." },
  openEventViewer: { title: "Open Event Viewer", detail: "Open Windows Event Viewer for local log inspection. No raw log messages are sent to AI." },
};
async function currentPowerPlan() {
  const { stdout } = await exec(powercfg, ["/getactivescheme"], { windowsHide: true, timeout: 8000 });
  const id = stdout.match(/[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}/i)?.[0]?.toLowerCase();
  if (!id) throw new Error("Could not verify the current power plan.");
  return id;
}
async function launch(command: string, args: string[]) {
  await new Promise<void>((resolve, reject) => {
    const child = spawn(command, args, { detached: true, stdio: "ignore", windowsHide: false });
    child.once("error", reject); child.once("spawn", () => { child.unref(); resolve(); });
  });
}
export async function remediate(action: string, token?: string) {
  // Offer only named, reviewable actions; a health warning never authorizes an automatic repair.
  if (process.platform !== "win32") throw new Error("These remediation actions require Windows.");
  if (!Object.hasOwn(HEALTH_ACTIONS, action)) throw new Error("Unsupported corrective action. Shell commands, process termination, repairs, reboots and deletion are not allowed.");
  const store = createStore();
  const description = HEALTH_ACTIONS[action];
  let before = "", target = "";
  if (["setBalanced", "restorePowerPlan"].includes(action)) {
    before = await currentPowerPlan();
    target = action === "setBalanced" ? balanced : store.setting<{ before?: string }>("lastPowerChange", {}).before || "";
    if (!/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/.test(target)) throw new Error("No verified previous plan is available.");
    if (target === before) return { ok: true, message: "The requested power plan is already active. No change made." };
  }
  const approval = approvalFor({ action: "healthRemediation", remedy: action, before, target, ...(token ? { approvalToken: token } : {}) }, `${description.title}: ${description.detail}${target ? ` Current plan ${before}; target ${target}.` : ""}`);
  if (approval) { store.event("permission", `Requested approval: ${description.title}. No system change yet.`); return { ok: false, approval }; }
  try {
    if (target) {
      await exec(powercfg, ["/setactive", target], { windowsHide: true, timeout: 10000 });
      const after = await currentPowerPlan();
      if (after !== target) throw new Error("Windows did not confirm the requested active plan.");
      if (action === "setBalanced") store.set("lastPowerChange", { before, after, time: new Date().toISOString() });
      store.event("action", `${description.title}: active plan verified changed from ${before} to ${after}. Workload/battery improvement is not established; observe subsequent telemetry.`);
      store.set("resample", Date.now());
      return { ok: true, message: "Active power plan verified. Check subsequent telemetry; this does not prove the underlying issue is fixed." };
    }
    if (action === "openTaskManager") await launch(path.join(windows, "System32", "Taskmgr.exe"), []);
    if (action === "openEventViewer") await launch(path.join(windows, "System32", "mmc.exe"), [path.join(windows, "System32", "eventvwr.msc")]);
    if (action === "openPowerSettings") await launch(path.join(windows, "explorer.exe"), ["ms-settings:powersleep"]);
    if (action === "openStorageSettings") await launch(path.join(windows, "explorer.exe"), ["ms-settings:storagesense"]);
    store.event("action", `${description.title}: Windows accepted the launch request. No corrective setting was changed.`);
    return { ok: true, message: "Windows accepted the launch request. No automated repair was performed." };
  } catch (error) {
    store.event("action-failed", `${description.title} failed. No repair is assumed.`);
    throw error;
  }
}
