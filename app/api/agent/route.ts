import { readFile } from "node:fs/promises";
import path from "node:path";
import { approvalFor, assertLocalRequest, readControlBody } from "@/lib/controlSecurity";
import { createTask, executeTaskStep, changeTask, taskById, pauseActiveTasks } from "@/lib/agentRunner";
import { listRecords } from "@/lib/assistantStore";
import type { AgentTask } from "@/lib/agentTypes";
import { ensureAgentWorker, workerStatus } from "@/lib/agentWorker";

export const runtime = "nodejs";
export async function POST(request: Request) {
  // Planning, review and execution are separate requests so suggesting a plan does not run it.
  try {
    assertLocalRequest(request);
    ensureAgentWorker(new URL(request.url).origin);
    const body = JSON.parse((await readControlBody(request, 12000)).toString());
    if (body.action === "pauseAll") { pauseActiveTasks(); return Response.json({ ok: true }); }
    if (body.action === "list") return Response.json({ tasks: listRecords<AgentTask>("task"), worker: workerStatus() });
    if (body.action === "plan") return Response.json({ task: await createTask(String(body.goal || ""), undefined, request.signal) });
    const task = taskById(String(body.id || ""));
    if (body.action === "get") return Response.json({ task });
    if (body.action === "replan") {
      const goal = String(body.goal || task.goal);
      const approval = approvalFor({ action: "replan", taskId: task.id, goal, observations: JSON.stringify(task.steps.filter(step => step.result)), ...(typeof body.token === "string" ? { approvalToken: body.token } : {}) }, "Send this task's goal and all completed step results to your configured AI provider to create a revised plan. Review the tool results for private data first.");
      if (approval) return Response.json({ approval });
      return Response.json({ task: await createTask(goal, task, request.signal) });
    }
    if (body.action === "report") {
      if (!task.report || !/^[\w-]+\.md$/.test(task.report)) throw new Error("No generated report yet.");
      return Response.json({ filename: task.report, text: await readFile(path.join(process.cwd(), ".ultron", "reports", task.report), "utf8") });
    }
    if (["run", "pause", "cancel", "manualDone"].includes(body.action)) return Response.json({ task: changeTask(task.id, body.action) });
    if (body.action === "step") return Response.json({ task: await executeTaskStep(task.id, new URL(request.url).origin, typeof body.token === "string" ? body.token : undefined) });
    throw new Error("Unsupported agent request.");
  } catch (error) { return Response.json({ error: error instanceof Error ? error.message : "Agent request failed." }, { status: 400 }); }
}
