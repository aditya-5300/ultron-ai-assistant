import { assertWorkerRequest, dispatchAgentTasks } from "@/lib/agentWorker";

export const runtime = "nodejs";
export async function POST(request: Request) {
  // Only the authenticated local dispatcher may ask this endpoint to advance ready work.
  try { assertWorkerRequest(request); }
  catch { return Response.json({ error: "Worker authorization required." }, { status: 403 }); }
  await dispatchAgentTasks(new URL(request.url).origin);
  return Response.json({ ok: true });
}
