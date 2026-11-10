export interface SavedMessage { id: number; role: "user" | "assistant"; content: string; }
export interface Preferences { name: string; instructions: string; shareMemory: boolean; }
export interface MemoryItem { id: string; title: string; text: string; updated: string; }
export interface Conversation { id: string; title: string; messages: SavedMessage[]; updated: string; }
export interface AgentEvidence { state: "verified" | "acknowledged" | "user_reported"; detail: string; time: string; }
export interface AgentStep { title: string; tool: string; args: Record<string, string | number | boolean>; result?: string; evidence?: AgentEvidence; started?: string; }
export interface AgentEvent { time: string; detail: string; }
export interface AgentTask {
  id: string; goal: string; title: string; steps: AgentStep[]; index: number;
  status: "review" | "ready" | "running" | "approval" | "paused" | "failed" | "completed" | "cancelled" | "needs_input";
  questions: string[]; timeline: AgentEvent[]; created: string; updated: string;
  approval?: { token: string; label: string; expires: number; kind: "agent" | "control" };
  snapshot?: string; deviceId?: string; error?: string; report?: string;
  execution?: "background";
  exportedReport?: string;
}
export interface MemoryState { preferences: Preferences; conversations: Conversation[]; knowledge: MemoryItem[]; tasks: AgentTask[]; }
