import { createHash, randomUUID } from "node:crypto";

export type ControlPayload = Record<string, unknown> & { action: string };

interface ControlState {
  approvals: Map<string, { fingerprint: string; expires: number }>;
  events: Array<{ id: string; time: string; action: string; ok: boolean; detail: string }>;
  requests: number[];
}

const controlGlobal = globalThis as typeof globalThis & { ultronControlState?: ControlState };
const state: ControlState = controlGlobal.ultronControlState ??= { approvals: new Map(), events: [], requests: [] };

export function assertLocalRequest(request: Request) {
  // Only pages on this computer may use these controls; visits from other websites are rejected.
  const url = new URL(request.url);
  const host = request.headers.get("host") || "";
  if (!/^(localhost|127\.0\.0\.1|\[::1\])(?::\d+)?$/.test(host) || request.headers.get("origin") !== `${url.protocol}//${host}` || request.headers.get("sec-fetch-site") === "cross-site") throw new Error("System control requires a same-origin localhost request.");
  const now = Date.now();
  state.requests = state.requests.filter(time => now - time < 60000);
  if (state.requests.length >= 100) throw new Error("Too many control requests. Wait a minute and retry.");
  state.requests.push(now);
}

export function approvalFor(payload: ControlPayload, label: string) {
  // Permission lasts one minute, works once, and applies only to the exact action shown to the user.
  const now = Date.now();
  for (const [token, item] of state.approvals) if (item.expires <= now) state.approvals.delete(token);
  const canonical = Object.fromEntries(Object.entries(payload).filter(([key]) => key !== "approvalToken").sort(([first], [second]) => first.localeCompare(second)));
  const fingerprint = createHash("sha256").update(JSON.stringify(canonical)).digest("hex");
  if (typeof payload.approvalToken === "string") {
    const pending = state.approvals.get(payload.approvalToken);
    state.approvals.delete(payload.approvalToken);
    if (!pending || pending.expires <= now || pending.fingerprint !== fingerprint) throw new Error("Approval expired, was already used, or does not match this action. Request the action again.");
    return null;
  }
  if (state.approvals.size >= 40) throw new Error("Too many pending approvals. Wait a minute and retry.");
  const token = randomUUID();
  const expires = now + 60000;
  state.approvals.set(token, { fingerprint, expires });
  return { token, label, expires };
}

export function recordControl(action: string, ok: boolean, detail: string) {
  state.events.unshift({ id: randomUUID(), time: new Date().toISOString(), action, ok, detail: detail.slice(0, 180) });
  state.events = state.events.slice(0, 50);
}

export function controlEvents() { return state.events.slice(); }

export function validateControlPayload(value: unknown): ControlPayload {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Expected an action object.");
  const payload = value as Record<string, unknown>;
  if (typeof payload.action !== "string" || !/^[a-zA-Z]+$/.test(payload.action)) throw new Error("A valid action is required.");
  for (const [key, field] of Object.entries(payload)) {
    if (typeof field === "string" && field.length > 4096) throw new Error(`${key} is too long.`);
    if (!["string", "number", "boolean"].includes(typeof field) && field !== undefined) throw new Error(`Invalid field: ${key}.`);
    if (typeof field === "number" && !Number.isFinite(field)) throw new Error(`Invalid number: ${key}.`);
  }
  return payload as ControlPayload;
}

export async function readControlBody(request: Request, limit: number) {
  if (!request.body) throw new Error("A request body is required.");
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > limit) { await reader.cancel(); throw new Error("Request is too large."); }
    chunks.push(value);
  }
  return Buffer.concat(chunks);
}
