import { randomUUID } from "node:crypto";
import { assertLocalRequest, readControlBody } from "@/lib/controlSecurity";
import { clearMemory, deleteRecord, getRecord, memoryState, putRecord, redactSecrets } from "@/lib/assistantStore";
import type { AgentTask, SavedMessage } from "@/lib/agentTypes";

export const runtime = "nodejs";
export async function POST(request: Request) {
  // Store preferences and chosen content locally, with an explicit confirmation for clearing all memory.
  try {
    assertLocalRequest(request);
    const body = JSON.parse((await readControlBody(request, 300000)).toString());
    if (body.action === "state") return Response.json(memoryState());
    if (body.action === "preferences") {
      putRecord("preferences", "user", { name: redactSecrets(String(body.name || "Aditya")).slice(0, 60), instructions: redactSecrets(String(body.instructions || "")).slice(0, 1500), shareMemory: body.shareMemory === true });
    } else if (body.action === "conversation") {
      if (typeof body.id !== "string" || !/^[\w-]{1,80}$/.test(body.id) || !Array.isArray(body.messages)) throw new Error("Invalid conversation.");
      const messages: SavedMessage[] = body.messages.slice(-100).map((message: SavedMessage) => {
        if (!message || !["user", "assistant"].includes(message.role) || typeof message.content !== "string" || !Number.isFinite(message.id)) throw new Error("Invalid message.");
        return { id: message.id, role: message.role, content: redactSecrets(message.content).slice(0, 12000) };
      });
      putRecord("conversation", body.id, { id: body.id, title: (messages.find(message => message.role === "user")?.content || "New conversation").slice(0, 80), messages, updated: new Date().toISOString() });
    } else if (body.action === "knowledge") {
      if (typeof body.text !== "string" || !body.text.trim()) throw new Error("Attach a document first.");
      const id = randomUUID();
      putRecord("knowledge", id, { id, title: redactSecrets(String(body.title || "Document")).slice(0, 160), text: redactSecrets(body.text).slice(0, 24000), updated: new Date().toISOString() });
    } else if (body.action === "delete") {
      if (!["conversation", "knowledge", "task"].includes(body.bucket) || typeof body.id !== "string") throw new Error("Invalid memory item.");
      if (body.bucket === "task" && getRecord<AgentTask>("task", body.id)?.status === "running") throw new Error("Stop the running task before deleting it.");
      deleteRecord(body.bucket, body.id);
    } else if (body.action === "clear") {
      if (body.confirm !== "DELETE LOCAL MEMORY") throw new Error("Type the exact deletion phrase.");
      if (memoryState().tasks.some(task => task.status === "running")) throw new Error("Stop running tasks before clearing memory.");
      clearMemory();
    } else throw new Error("Unsupported memory action.");
    return Response.json({ ok: true });
  } catch (error) { return Response.json({ error: error instanceof Error ? error.message : "Memory request failed." }, { status: 400 }); }
}
