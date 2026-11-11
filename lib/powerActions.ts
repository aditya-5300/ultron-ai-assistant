import { execFile } from "node:child_process";
import { randomUUID } from "node:crypto";
import path from "node:path";
import { promisify } from "node:util";
import { approvalFor, recordControl, type ControlPayload } from "./controlSecurity";
import type { ControlReply, PowerCountdown } from "./controlTypes";

const exec = promisify(execFile);
const delay = 60_000;
type Pending = PowerCountdown & { timer: ReturnType<typeof setTimeout> };
type PowerState = { pending?: Pending; running?: boolean; notice?: string };
const server = globalThis as typeof globalThis & { ultronPowerState?: PowerState };
const state = server.ultronPowerState ??= {};

async function submitToWindows(id: string) {
  const pending = state.pending;
  if (!pending || pending.id !== id) return;
  state.pending = undefined;
  if (Date.now() - pending.deadline > 5_000) {
    state.notice = "Power action expired without running because the countdown was interrupted.";
    recordControl("schedulePower", false, state.notice);
    return;
  }
  state.running = true;
  try {
    // A nonzero Windows timeout implies force-close. ULTRON provides the countdown instead.
    const executable = path.win32.join(process.env.SystemRoot || "C:\\Windows", "System32", "shutdown.exe");
    await exec(executable, [pending.kind === "shutdown" ? "/s" : "/r", "/t", "0"], { windowsHide: true, timeout: 10_000 });
    recordControl("schedulePower", true, `Windows accepted the ${pending.kind} request without force-close.`);
  } catch {
    state.notice = "Windows did not confirm the power request. No retry or elevation was attempted.";
    recordControl("schedulePower", false, state.notice);
  } finally {
    state.running = false;
  }
}

export function executePowerAction(payload: ControlPayload): ControlReply {
  const { action, kind, countdownId } = payload;
  const allowed = action === "schedulePower" ? ["action", "kind", "approvalToken"]
    : action === "cancelPower" ? ["action", "kind", "countdownId"] : ["action"];
  if (Object.keys(payload).some(key => !allowed.includes(key))) throw new Error("Unexpected power action option.");
  if (action === "powerStatus") {
    const pending = state.pending;
    return { ok: true, power: pending ? { id: pending.id, kind: pending.kind, deadline: pending.deadline } : null, powerNotice: state.notice };
  }
  if (process.platform !== "win32") throw new Error("Power commands require Windows.");
  if (action === "cancelPower") {
    const pending = state.pending;
    if (!pending || (countdownId !== undefined && countdownId !== pending.id) || (kind !== undefined && kind !== pending.kind)) {
      throw new Error("No matching ULTRON power countdown to cancel.");
    }
    clearTimeout(pending.timer);
    state.pending = undefined;
    return { ok: true, power: null, message: `Cancelled the pending ${pending.kind}.` };
  }
  if (action !== "schedulePower" || (kind !== "shutdown" && kind !== "restart")) throw new Error("Choose shutdown or restart.");
  if (state.pending || state.running) throw new Error("A power action is already pending or being sent to Windows.");
  const approval = approvalFor(payload, `Request a Windows ${kind} in 60 seconds. Save your work first. You can cancel during the countdown. ULTRON will not force apps to close.`);
  if (approval) return { ok: false, approval };
  const id = randomUUID();
  const deadline = Date.now() + delay;
  state.notice = undefined;
  state.pending = { id, kind, deadline, timer: setTimeout(() => void submitToWindows(id), delay) };
  return { ok: true, power: { id, kind, deadline }, message: `${kind === "shutdown" ? "Shutdown" : "Restart"} scheduled in 60 seconds. Use Cancel in the countdown panel or say “ULTRON, cancel ${kind}”.` };
}
