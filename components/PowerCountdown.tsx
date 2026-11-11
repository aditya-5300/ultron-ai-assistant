"use client";

import { useEffect, useRef, useState } from "react";
import type { ControlReply } from "@/lib/controlTypes";
import styles from "./AssistantDashboard.module.css";

async function powerRequest(payload: Record<string, unknown>, signal?: AbortSignal): Promise<ControlReply> {
  const response = await fetch("/api/system-control", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  });
  const reply: ControlReply = await response.json();
  if (!response.ok || !reply.ok) throw new Error(reply.error || "Power status unavailable.");
  return reply;
}

export default function PowerCountdown() {
  const [pending, setPending] = useState<ControlReply["power"]>();
  const [notice, setNotice] = useState("");
  const [connectionLost, setConnectionLost] = useState(false);
  const [now, setNow] = useState(0);
  const [cancelling, setCancelling] = useState(false);
  const revision = useRef(0);

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      const observedRevision = revision.current;
      try {
        const reply = await powerRequest({ action: "powerStatus" }, controller.signal);
        if (!controller.signal.aborted && observedRevision === revision.current) {
          setPending(reply.power);
          setNotice(reply.powerNotice || "");
          setConnectionLost(false);
          setNow(Date.now());
        }
      } catch {
        if (!controller.signal.aborted && observedRevision === revision.current) setConnectionLost(true);
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(poll, 3_000);
      }
    };
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, []);

  useEffect(() => {
    if (!pending) return;
    const timer = setInterval(() => setNow(Date.now()), 1_000);
    return () => clearInterval(timer);
  }, [pending?.id]);

  async function cancel() {
    if (!pending || cancelling) return;
    revision.current++;
    setCancelling(true);
    try {
      await powerRequest({ action: "cancelPower", countdownId: pending.id });
      setPending(null);
      setNotice("");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Could not confirm cancellation.");
    } finally {
      revision.current++;
      setCancelling(false);
    }
  }

  if (!pending && !notice) return null;
  const seconds = pending ? Math.max(0, Math.ceil((pending.deadline - now) / 1_000)) : 0;
  return <section className={styles.approvalPanel} aria-label="PC power countdown">
    <div>
      {pending && <><strong>{pending.kind === "shutdown" ? "Shutdown" : "Restart"} pending</strong>
        <p>{seconds > 0 ? `About ${seconds} seconds remaining. Save your work now.` : "Countdown ended. Checking Windows request status."}</p>
        {connectionLost && <p role="alert">Connection lost. Cancellation has not been confirmed.</p>}
      </>}
      {notice && <p role="alert">{notice}</p>}
    </div>
    {pending && <button type="button" disabled={cancelling} onClick={() => void cancel()}>{cancelling ? "Cancelling…" : `Cancel ${pending.kind}`}</button>}
  </section>;
}
