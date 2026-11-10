"use client";
import { useEffect, useState } from "react";
import type { MemoryState, Preferences } from "@/lib/agentTypes";
import { memoryRequest } from "@/lib/useConversationMemory";
import styles from "./AssistantDashboard.module.css";

export default function MemoryWorkspace({ state, refresh, onSelect, onClear, fileLabel, fileContext }: { state?: MemoryState; refresh: () => Promise<MemoryState>; onSelect: (id: string) => void; onClear: () => void; fileLabel: string; fileContext: string }) {
  // Let the user review local memory and choose whether any of it may be shared with AI.
  const [profile, setProfile] = useState<Preferences>({ name: "Aditya", instructions: "", shareMemory: false });
  const [status, setStatus] = useState("");
  const [phrase, setPhrase] = useState("");
  useEffect(() => { if (state) setProfile(state.preferences); }, [state]);
  async function mutate(payload: Record<string, unknown>) {
    try { await memoryRequest(payload); await refresh(); setStatus("Saved locally."); }
    catch (failure) { setStatus(failure instanceof Error ? failure.message : "Memory request failed."); }
  }
  return <section className={styles.contentPanel}><span className={styles.eyebrow}>LOCAL-FIRST · YOU OWN THE CONTEXT</span><h1>Memory & personalization</h1>
    <p className={styles.secondary}>Conversations, preferences, task history and chosen document text are stored in this project’s .ultron folder, unencrypted. Obvious key/password patterns are redacted, not all sensitive data. Do not save secrets. Deleting memory removes database records, not exported reports or backups.</p>
    <form className={styles.agentComposer} onSubmit={event => { event.preventDefault(); void mutate({ action: "preferences", ...profile }); }}>
      <label>Display name<input value={profile.name} maxLength={60} onChange={event => setProfile({ ...profile, name: event.target.value })} /></label>
      <label>Response preferences<textarea placeholder="Concise answers, TypeScript examples, metric units…" maxLength={1500} value={profile.instructions} onChange={event => setProfile({ ...profile, instructions: event.target.value })} /></label>
      <label className={styles.checkLabel}><input type="checkbox" checked={profile.shareMemory} onChange={event => setProfile({ ...profile, shareMemory: event.target.checked })} />Allow relevant saved document excerpts, preferences and recent conversation/task history to be sent to my AI provider for chat and planning.</label>
      <button type="submit">Save preferences</button>
    </form>
    <p role="status">{status}</p>
    <div className={styles.sectionHeading}><h2>Saved conversations</h2><button onClick={() => void refresh().catch(failure => setStatus(failure.message))}>Refresh memory</button></div>
    <div className={styles.memoryCards}>{state?.conversations.map(chat => <article key={chat.id}><strong>{chat.title}</strong><small>{chat.messages.length} messages · {new Date(chat.updated).toLocaleDateString()}</small><div className={styles.inlineActions}><button onClick={() => onSelect(chat.id)}>Resume chat</button><button onClick={() => { if (window.confirm("Delete this saved conversation?")) { onClear(); void mutate({ action: "delete", bucket: "conversation", id: chat.id }); } }}>Delete</button></div></article>)}</div>
    <h2>Document knowledge</h2><p className={styles.secondary}>Local keyword retrieval, not embeddings. Saved excerpts are shared with AI only when the sharing option above is enabled, or when you approve a report step that uses them.</p>
    <button disabled={!fileContext} onClick={() => void mutate({ action: "knowledge", title: fileLabel, text: fileContext })}>Save attached document to knowledge</button>
    <div className={styles.memoryCards}>{state?.knowledge.map(document => <article key={document.id}><strong>{document.title}</strong><details><summary>Preview saved text</summary><pre>{document.text.slice(0, 2000)}</pre></details><button onClick={() => void mutate({ action: "delete", bucket: "knowledge", id: document.id })}>Forget document</button></article>)}</div>
    <div className={styles.agentNotice}><h2>Clear local memory</h2><p>Stops no external actions and does not delete generated report files. Type DELETE LOCAL MEMORY to erase saved conversations, preferences, documents and task records.</p><input aria-label="Memory deletion phrase" value={phrase} onChange={event => setPhrase(event.target.value)} /><button disabled={phrase !== "DELETE LOCAL MEMORY"} onClick={async () => { try { await memoryRequest({ action: "clear", confirm: phrase }); onClear(); await refresh(); setPhrase(""); setStatus("Local memory cleared."); } catch (failure) { setStatus(String(failure)); } }}>Delete local memory</button></div>
  </section>;
}
