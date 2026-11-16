"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import styles from "./MonitoringDashboard.module.css";
import shadow from "./ShadowDashboard.module.css";

type Finding = { id: string | null; subsystem: string; type: string; transition: string; state: string; severity: string;
  firstAt: number | null; at: number | null; evidenceAt: number | null; quality: string | null; definition: string | null;
  policy: string; horizon: number | null; reason: string | null; opening: string | null };
type Page = { items: Finding[]; next: number | null };
type Attempt = { at: number; state: string; code: string };
type Status = { state: string; stale?: boolean; compatibility: boolean | null; lastAttempt?: Attempt;
  checkpoint?: { namespace: string; asOf: number; ageMs: number; start: number; rows: number; anchorExists: boolean; cursorPresent: boolean } };
type Metrics = { available: boolean; attempts: number; successes: number; failures: number; lastSuccess?: Attempt; lastFailure?: Attempt;
  recentFailures: Attempt[]; latency?: { samples: number; latest: number; median: number; p95: number | null };
  counts: { state: string; subsystem: string; count: number }[]; raw: number; validated: number; persisted: number;
  repeated: number; staleRejected: number; duplicateRejected: number; rebases: number; busy: number; insufficient: number;
  eligibility?: { policy: string; eligible: number; historical: number; ambiguous: number };
  scheduler?: { accepted: number; skipped: number; completed: number; failed: number; backoffProbe: boolean } };
const format = (value?: number | null) => value == null ? "Not recorded" : new Date(value).toLocaleString();
const value = (number?: number | null, unit = "") => number == null ? "Not recorded" : `${Number(number.toFixed(2))}${unit}`;
const subsystem: Record<string, string> = { cpu: "CPU / workload", disk_capacity: "Disk capacity", process: "Process responsiveness" };
const opening: Record<string, string> = { ELIGIBLE: "Hypothetically eligible", HISTORICAL_BOOTSTRAP: "Historical bootstrap",
  HISTORICAL_REBASE: "Historical rebase", AMBIGUOUS_REBASE: "Continuity unknown after rebase" };

async function read<T>(query: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(`/api/monitor/shadow?${query}`, { signal, headers: { "X-ULTRON-Shadow-Read": "1" }, cache: "no-store" });
  if (!response.ok) throw new Error("Shadow records could not be read.");
  return response.json();
}

export default function ShadowDashboard() {
  const [overview, setOverview] = useState<{ status: Status; metrics: Metrics }>();
  const [findings, setFindings] = useState<Page>();
  const [timeline, setTimeline] = useState<Page>();
  const [selected, setSelected] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);
  const [historyOffset, setHistoryOffset] = useState(0);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([read<Status>("view=status", controller.signal), read<Metrics>("view=metrics", controller.signal)])
      .then(([status, metrics]) => setOverview({ status, metrics }))
      .catch(() => { if (!controller.signal.aborted) setError("Could not load inference status."); });
    return () => controller.abort();
  }, [revision]);
  useEffect(() => {
    const controller = new AbortController();
    read<Page>(`view=findings&limit=10&offset=${offset}`, controller.signal).then(setFindings)
      .catch(() => { if (!controller.signal.aborted) setError("Could not load findings."); });
    return () => controller.abort();
  }, [offset, revision]);
  useEffect(() => {
    const controller = new AbortController();
    read<Page>(`view=transitions&limit=10&offset=${historyOffset}${selected ? `&finding=${selected}` : ""}`, controller.signal).then(setTimeline)
      .catch(() => { if (!controller.signal.aborted) setError("Could not load the lifecycle timeline."); });
    return () => controller.abort();
  }, [selected, historyOffset, revision]);

  const status = overview?.status;
  const metrics = overview?.metrics;
  const checkpoint = status?.checkpoint;
  const activity = metrics?.latency;
  function showTimeline(id: string | null) {
    if (id === selected && historyOffset === 0) return;
    setTimeline(undefined); setSelected(id); setHistoryOffset(0);
  }
  function pageControls(page: Page | undefined, current: number, update: (offset: number) => void) {
    return <div className={shadow.actions}><button disabled={!current} onClick={() => update(Math.max(0, current - 10))}>Previous</button>
      <span>Page {Math.floor(current / 10) + 1}</span>
      <button disabled={page?.next == null} onClick={() => { if (page?.next != null) update(page.next); }}>Next</button></div>;
  }

  return <main className={styles.page}><div className={styles.shell}>
    <header className={styles.header}><Link className={styles.brand} href="/monitor">ULTRON<span>SHADOW OBSERVABILITY</span></Link>
      <Link href="/monitor">Deterministic monitoring ↗</Link></header>
    <div className={styles.heading}><div><span className={styles.eyebrow}>EXPERIMENTAL INFERENCE</span>
      <h1>Experimental Shadow Findings — Not Used for Alerts</h1>
      <p>Read-only replay evidence. Inference status does not establish device health, confirmed incidents, or hardware-failure predictions.</p></div>
      <button onClick={() => { setError(""); setRevision(revision + 1); }}>Refresh view</button></div>
    {error && <p role="alert" className={shadow.notice}>{error} Any displayed records may be from the last successful read.</p>}
    {!status && !error && <p role="status">Loading inference status…</p>}
    {status && <div className={shadow.grid}>
      <section><h2>Inference status</h2><strong className={shadow.badge}>{status.state}</strong>
        <dl><dt>Compatibility</dt><dd>{status.compatibility == null ? "Not verified" : status.compatibility ? "Last handshake passed" : "Last handshake incompatible"}</dd>
          <dt>Last attempt</dt><dd>{format(status.lastAttempt?.at)} {status.lastAttempt?.code}</dd>
          <dt>Last success</dt><dd>{format(metrics?.lastSuccess?.at)}</dd><dt>Last failure</dt><dd>{format(metrics?.lastFailure?.at)} {metrics?.lastFailure?.code}</dd>
          <dt>Latest duration / median / p95</dt><dd>{value(activity?.latest, " ms")} / {value(activity?.median, " ms")} / {value(activity?.p95, " ms")}</dd></dl>
        {status.stale && <p>Stored inference status is stale.</p>}
        {metrics?.recentFailures?.length ? <ul>{metrics.recentFailures.map((attempt, index) => <li key={index}>{format(attempt.at)} · {attempt.code}</li>)}</ul> : null}
      </section>
      <section><h2>Replay checkpoint</h2>{checkpoint ? <dl>
        <dt>Namespace</dt><dd className={shadow.code}>{checkpoint.namespace}</dd>
        <dt>Coverage start</dt><dd>{format(checkpoint.start)} · {value(checkpoint.rows)} rows</dd>
        <dt>Last processed sample</dt><dd>{format(checkpoint.asOf)}</dd>
        <dt>Age</dt><dd>{value(checkpoint.ageMs == null ? null : checkpoint.ageMs / 60000, " min")}</dd>
        <dt>Cursor / anchor</dt><dd>{checkpoint.cursorPresent ? "Present" : "No events yet"} / {checkpoint.anchorExists ? "Available" : "Not retained"}</dd>
      </dl> : <p>No replay checkpoint recorded.</p>}</section>
      <section><h2>Reliability</h2>{metrics?.available ? <dl>
        <dt>Recent attempts / failures</dt><dd>{metrics.attempts} / {metrics.failures}</dd>
        <dt>Raw / validated / persisted</dt><dd>{value(metrics.raw)} / {value(metrics.validated)} / {value(metrics.persisted)}</dd>
        <dt>Repeated / stale / duplicate OPEN rejected</dt><dd>{value(metrics.repeated)} / {metrics.staleRejected} / {metrics.duplicateRejected}</dd>
        <dt>Rebases / busy / insufficient history</dt><dd>{metrics.rebases} / {metrics.busy} / {metrics.insufficient}</dd>
        <dt>Simulated eligible / historical openings</dt><dd>{value(metrics.eligibility?.eligible)} / {value(metrics.eligibility?.historical)}</dd>
        <dt>Ambiguous rebase openings withheld</dt><dd>{value(metrics.eligibility?.ambiguous)}</dd>
        <dt>Scheduler at dispatch</dt><dd>{metrics.scheduler ? `${metrics.scheduler.accepted} accepted, ${metrics.scheduler.skipped} skipped, ${metrics.scheduler.completed} completed, ${metrics.scheduler.failed} failed${metrics.scheduler.backoffProbe ? " · backoff probe" : ""}` : "Not recorded"}</dd>
      </dl> : <p>No durable inference metrics available.</p>}
      <p>Counts reflect retained records. A busy database can prevent recording an attempt.</p></section>
    </div>}
    <div className={shadow.grid}>
      <section><h2>Shadow findings</h2>
        {!!metrics?.counts?.length && <p>{metrics.counts.map(row => `${subsystem[row.subsystem]} ${row.state}: ${row.count}`).join(" · ")}</p>}
        {!findings ? <p>Loading findings…</p> : !findings.items.length ? <p>No persisted findings on this page. This is not a healthy verdict.</p> :
          <ul>{findings.items.map(item => <li key={item.id}><button onClick={() => showTimeline(item.id)}>{subsystem[item.subsystem]} · {item.type}</button>
            <div>{item.state} in replay · {item.severity} · {format(item.at)}</div>
            <small>{item.opening ? opening[item.opening] : "Opening eligibility not recorded"} · not a live incident</small></li>)}</ul>}
        {pageControls(findings, offset, next => { setFindings(undefined); setOffset(next); })}
      </section>
      <section><h2>Lifecycle inspector</h2><p>OPEN → UPDATE → ESCALATE → CLOSE. Updates and escalation do not open a second incident.</p>
        {selected && <><p className={shadow.code}>{selected}</p><button onClick={() => showTimeline(null)}>Show all recent transitions</button></>}
        {!timeline ? <p>Loading transitions…</p> : !timeline.items.length ? <p>No recorded transitions on this page.</p> :
          <ol>{timeline.items.map((item, index) => <li key={index}>
            <strong>{subsystem[item.subsystem]} · {item.type} · {item.transition}</strong><div>{format(item.at)} · {item.state} · {item.severity}</div>
            <div>First evidence {format(item.firstAt)} · latest {format(item.evidenceAt)} · {item.quality ?? "unknown"}</div>
            <small>{item.policy} · {item.definition}{item.horizon ? ` · ${item.horizon / 60} min horizon` : ""}{item.reason ? ` · ${item.reason.replaceAll("_", " ")}` : ""}</small>
          </li>)}</ol>}
        {pageControls(timeline, historyOffset, next => { setTimeline(undefined); setHistoryOffset(next); })}
      </section>
    </div>
  </div></main>;
}
