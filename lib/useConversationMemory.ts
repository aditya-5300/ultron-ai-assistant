"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import type { MemoryState, SavedMessage } from "./agentTypes";

export async function memoryRequest(payload: Record<string, unknown>) {
  const response = await fetch("/api/memory", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Local memory request failed.");
  return data;
}
export function useConversationMemory(messages: SavedMessage[], busy: boolean, restore: (messages: SavedMessage[]) => void) {
  // Restore the latest chat on arrival and save finished replies without saving every streamed letter.
  const [state, setState] = useState<MemoryState>();
  const [error, setError] = useState("");
  const [ready, setReady] = useState(false);
  const conversationId = useRef("");
  const restoreRef = useRef(restore);
  restoreRef.current = restore;
  const refresh = useCallback(async () => {
    const data = await memoryRequest({ action: "state" }) as MemoryState;
    setState(data); return data;
  }, []);
  useEffect(() => {
    let active = true;
    void memoryRequest({ action: "state" }).then((data: MemoryState) => {
      if (!active) return;
      setState(data);
      const latest = data.conversations[0];
      conversationId.current = latest?.id || crypto.randomUUID();
      if (latest?.messages.length) restoreRef.current(latest.messages);
      setReady(true);
    }).catch(failure => { if (active) setError(failure.message); });
    return () => { active = false; };
  }, []);
  useEffect(() => {
    if (!ready || busy || !messages.some(message => message.role === "user")) return;
    const id = conversationId.current;
    const timer = setTimeout(() => {
      void memoryRequest({ action: "conversation", id, messages: messages.filter(message => message.content.trim()) }).then(() => setError("")).catch(failure => setError(failure.message));
    }, 700);
    return () => clearTimeout(timer);
  }, [messages, ready, busy]);
  const select = (id: string) => {
    const chat = state?.conversations.find(item => item.id === id);
    if (chat) { conversationId.current = id; restoreRef.current(chat.messages); }
  };
  const newChat = () => { conversationId.current = crypto.randomUUID(); };
  return { state, refresh, error, ready, select, newChat };
}
