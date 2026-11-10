"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import type { AgentTask } from "@/lib/agentTypes";
import styles from "./AssistantDashboard.module.css";

export async function agentRequest(payload: Record<string, unknown>, signal?: AbortSignal) {
  const response = await fetch("/api/agent", { method: "POST", signal, headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Agent request failed.");
  return data as { task: AgentTask; tasks: AgentTask[]; filename: string; text: string; worker?: { running: boolean; heartbeat?: string; error?: string }; approval?: { token: string; label: string } };
}
export default function AgentWorkspace({ visible, initialGoal, onState, stopSignal }: { visible: boolean; initialGoal: { text: string; key: number }; onState: (busy: boolean) => void; stopSignal: number }) {
  // Show saved plans and permissions here; the background dispatcher advances reviewed tasks.
  const [goal, setGoal] = useState("");
  const [tasks, setTasks] = useState<AgentTask[]>([]);
  const [task, setTask] = useState<AgentTask>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [replanApproval, setReplanApproval] = useState<{ token: string; label: string }>();
  const [worker, setWorker] = useState<{ running: boolean; heartbeat?: string; error?: string }>();
  const running = useRef(false);
  const selected = useRef("");
  const planning = useRef<AbortController | null>(null);
  const refresh = useCallback(async () => {
    const data = await agentRequest({ action: "list" });
    setTasks(data.tasks); setWorker(data.worker);
    const current = data.tasks.find(item => item.id === selected.current);
    if (current) setTask(current);
  }, []);
  useEffect(() => {
    if (!visible) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try { await refresh(); } catch (failure) { if (!stopped) setError(failure instanceof Error ? failure.message : "Status unavailable."); }
      if (!stopped) timer = setTimeout(poll, 3000);
    };
    void poll();
    return () => { stopped = true; clearTimeout(timer); };
  }, [visible, refresh]);
  useEffect(() => { if (initialGoal.text) { setGoal(initialGoal.text); void plan(initialGoal.text); } }, [initialGoal]);
  useEffect(() => { if (stopSignal) { planning.current?.abort(); void agentRequest({ action: "pauseAll" }).then(() => refresh()).catch(failure => setError(String(failure))); } }, [stopSignal, refresh]);
  useEffect(() => () => { running.current = false; planning.current?.abort(); }, []);
  const showTask = (next: AgentTask) => { if (selected.current === next.id) setTask(next); };
  async function action(kind: string, token?: string) {
    if (running.current && !["pause", "cancel"].includes(kind)) return;
    if (!task) return;
    setError("");
    if (["pause", "cancel"].includes(kind)) {
      running.current = false;
      try { showTask((await agentRequest({ action: kind, id: task.id })).task); }
      catch (failure) { setError(failure instanceof Error ? failure.message : "Stop failed."); }
      return;
    }
    running.current = true; setBusy(true); onState(true);
    try {
      const data = await agentRequest({ action: kind, id: task.id, token, ...(kind === "replan" ? { goal } : {}) });
      if (data.approval) { setReplanApproval(data.approval); return; }
      setReplanApproval(undefined);
      const next = data.task;
      if (kind === "replan") selected.current = next.id;
      showTask(next);
      await refresh();
    } catch (failure) { setError(failure instanceof Error ? failure.message : "Task request failed."); }
    finally { running.current = false; setBusy(false); onState(false); }
  }
  async function plan(requestedGoal = goal) {
    if (running.current) return;
    running.current = true; setBusy(true); onState(true); setError("");
    const controller = new AbortController(); planning.current = controller;
    try {
      const next = (await agentRequest({ action: "plan", goal: requestedGoal }, controller.signal)).task;
      selected.current = next.id; setTask(next); await refresh();
    } catch (failure) { setError(controller.signal.aborted ? "Planning cancelled. No tools were run." : failure instanceof Error ? failure.message : "Planning failed."); }
    finally { planning.current = null; running.current = false; setBusy(false); onState(false); }
  }
  async function download() {
    if (!task) return;
    try {
      const data = await agentRequest({ action: "report", id: task.id });
      const url = URL.createObjectURL(new Blob([data.text], { type: "text/markdown" }));
      const link = document.createElement("a"); link.href = url; link.download = data.filename; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (failure) { setError(failure instanceof Error ? failure.message : "Report download failed."); }
  }
  return <section className={styles.contentPanel} hidden={!visible}>
    <span className={styles.eyebrow}>LEVEL 4 · PLAN → REVIEW → ACT → VERIFY</span><h1>Agent tasks</h1>
    <p className={styles.secondary}>Background dispatcher: {worker?.error || (worker?.running ? "running" : "starting")}. Started tasks advance while this page is closed, as long as the local server runs. Every action still pauses for approval. Server restarts pause unfinished work for review.</p>
    <p className={styles.secondary}>Give ULTRON a goal. Planning sends your goal to your configured AI provider. Nothing executes until you review the plan. Each external action has a separate, one-use approval. Purchases, logins and destructive actions require manual takeover.</p>
    <form className={styles.agentComposer} onSubmit={event => { event.preventDefault(); void plan(); }}>
      <label htmlFor="agent-goal">Task goal or revised instructions</label><textarea id="agent-goal" placeholder="Research electric vehicles in India and make a cited report…" maxLength={3000} value={goal} onChange={event => setGoal(event.target.value)} />
      <div className={styles.inlineActions}><button disabled={busy || !goal.trim()} type="submit">{busy ? "Working…" : "Create plan"}</button><button type="button" onClick={() => setGoal("Book me a ticket. Ask me for the destination, dates, travelers and budget first.")}>Ticket planning</button><button type="button" onClick={() => setGoal("Find files named README in project, read the first matching document and make a report about it.")}>Document report</button></div>
      {busy && planning.current && <button type="button" onClick={() => planning.current?.abort()}>Cancel planning</button>}
    </form>
    {error && <p className={styles.agentError} role="alert">{error}</p>}
    <div className={styles.agentLayout}><aside className={styles.taskList}><h2>Saved tasks</h2><button disabled={busy} onClick={() => void refresh()}>Refresh history</button>{tasks.map(item => <button disabled={busy} key={item.id} aria-pressed={task?.id === item.id} onClick={async () => { selected.current = item.id; try { setTask((await agentRequest({ action: "get", id: item.id })).task); setGoal(item.goal); } catch (failure) { setError(String(failure)); } }}><strong>{item.title}</strong><small>{item.status} · {item.index}/{item.steps.length}</small></button>)}</aside>
    <div className={styles.taskDetail}>{task ? <>
      <div className={styles.sectionHeading}><h2>{task.title}</h2><span className={styles.badge}>{task.status.replaceAll("_", " ")}</span></div>
      <p className={styles.secondary}>{task.goal}</p>
      {!!task.questions.length && <div className={styles.agentNotice}><strong>More details needed</strong><ul>{task.questions.map(question => <li key={question}>{question}</li>)}</ul><p>Update the goal above with your answers, then create a new plan.</p></div>}
      <ol className={styles.planSteps}>{task.steps.map((step, index) => <li key={index} data-done={index < task.index}><div><strong>{step.title}</strong><small>{step.tool} · {index < task.index ? step.evidence?.state.replaceAll("_", " ") || "legacy result · unverified" : index === task.index ? "next" : "pending"}</small></div><code>{JSON.stringify(step.args)}</code>{step.evidence && <p className={styles.secondary}>{step.evidence.detail}</p>}{step.result && <details><summary>Tool result</summary><pre>{step.result}</pre></details>}</li>)}</ol>
      {task.error && <p role="alert" className={styles.agentError}>{task.error}</p>}
      {replanApproval && <div className={styles.approvalPanel} role="alertdialog" aria-label="Replanning data permission"><p>{replanApproval.label}</p><button disabled={busy} onClick={() => void action("replan", replanApproval.token)}>Approve sending observations</button><button onClick={() => setReplanApproval(undefined)}>Decline</button></div>}
      {task.approval && <div className={styles.approvalPanel} role="alertdialog" aria-label="Agent step approval"><div><strong>Permission required · exact step only</strong><p>{task.approval.label}</p><small>Expires in 60 seconds. Review data and destination. Approvals are not accepted through hands-free speech.</small></div><button disabled={busy} onClick={() => void action("step", task.approval?.token)}>Approve this step</button></div>}
      <div className={styles.inlineActions}>
        {task.status === "paused" && task.steps[task.index]?.tool === "manual" && <button disabled={busy} onClick={() => void action("manualDone")}>I completed this step manually</button>}
        {["review", "paused", "failed"].includes(task.status) && <button disabled={busy} onClick={() => void action("run")}>{task.status === "review" ? "Review complete · start" : "Retry current step"}</button>}
        {task.status === "ready" && <button disabled={busy} onClick={() => void action("step")}>Continue task</button>}
        {["running", "ready", "approval"].includes(task.status) && <button onClick={() => void action("pause")}>Pause</button>}
        {!["completed", "cancelled"].includes(task.status) && <button onClick={() => void action("cancel")}>Cancel task</button>}
        <button disabled={busy || !goal.trim()} onClick={() => void action("replan")}>Revise plan with observations</button>
        {task.report && <button onClick={() => void download()}>Download report</button>}
      </div>
      <h2>Activity timeline</h2><div className={styles.eventList} aria-live="polite">{task.timeline.slice().reverse().map((event, index) => <div key={index}><span>{new Date(event.time).toLocaleTimeString()}</span><p>{event.detail}</p></div>)}</div>
    </> : <div className={styles.empty}><h2>Plan before acting.</h2><p>Create a plan or select a saved task. Background tasks pause for permission, uncertainty, failures and manual takeover. ULTRON does not run arbitrary shell commands or make purchases.</p></div>}</div></div>
  </section>;
}
