import { spawn, type ChildProcess } from "node:child_process";
import { randomBytes, timingSafeEqual } from "node:crypto";
import path from "node:path";
import { listRecords } from "./assistantStore";
import { changeTask, executeTaskStep } from "./agentRunner";
import type { AgentTask } from "./agentTypes";

interface WorkerState {
  token: string; child?: ChildProcess; origin?: string; recovered: boolean;
  heartbeat?: string; error?: string; failures: number; dispatching: boolean;
}
const workerGlobal = globalThis as typeof globalThis & { ultronAgentWorker?: WorkerState };
const state = workerGlobal.ultronAgentWorker ??= { token: randomBytes(32).toString("hex"), recovered: false, failures: 0, dispatching: false };

export function workerStatus() {
  return { running: !!state.child && state.child.exitCode === null, heartbeat: state.heartbeat, error: state.error };
}

export function ensureAgentWorker(origin: string) {
  const url = new URL(origin);
  if (url.protocol !== "http:" || !["127.0.0.1", "localhost", "[::1]"].includes(url.hostname)) throw new Error("Agent worker requires a local HTTP server.");
  if (!state.recovered) {
    // After a server restart, ask for review instead of silently repeating unfinished actions.
    for (const task of listRecords<AgentTask>("task")) {
      if (task.execution === "background" && ["ready", "running", "approval"].includes(task.status)) changeTask(task.id, "pause");
    }
    state.recovered = true;
  }
  if (state.child || state.failures >= 5) return;
  state.origin = origin;
  const script = path.join(/* turbopackIgnore: true */ process.cwd(), "scripts", "agent-worker.cjs");
  const child = spawn(process.execPath, [script], {
    windowsHide: true, stdio: "ignore",
    env: { NODE_ENV: process.env.NODE_ENV, SystemRoot: process.env.SystemRoot, PATH: process.env.PATH, TEMP: process.env.TEMP, ULTRON_WORKER_TOKEN: state.token, ULTRON_WORKER_ORIGIN: origin, ULTRON_WORKER_PARENT: String(process.pid) },
  });
  state.child = child;
  child.once("error", () => { state.error = "Background dispatcher could not start. Review server configuration."; });
  child.once("close", () => {
    if (state.child !== child) return;
    state.child = undefined;
    state.failures += 1;
    state.error = "Background dispatcher stopped. Restarting with bounded backoff.";
    if (state.failures < 5) setTimeout(() => ensureAgentWorker(origin), state.failures * 3000).unref();
  });
  child.unref();
}

export function assertWorkerRequest(request: Request) {
  // A private, temporary token prevents ordinary website requests from impersonating the worker.
  const provided = Buffer.from(request.headers.get("authorization") || "");
  const expected = Buffer.from(`Bearer ${state.token}`);
  if (!state.origin || new URL(request.url).origin !== state.origin || provided.length !== expected.length || !timingSafeEqual(provided, expected)) throw new Error("Worker authorization required.");
}

export async function dispatchAgentTasks(origin: string) {
  // Pick the oldest ready task. Approval requests stay paused until the user responds.
  state.heartbeat = new Date().toISOString();
  state.error = undefined;
  if (state.dispatching) return;
  state.dispatching = true;
  try {
    const next = listRecords<AgentTask>("task").filter(task => task.execution === "background" && task.status === "ready")
      .sort((first, second) => first.updated.localeCompare(second.updated))[0];
    if (next) await executeTaskStep(next.id, origin);
  } finally { state.dispatching = false; }
}
