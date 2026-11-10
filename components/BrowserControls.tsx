import { useState } from "react";
import type { BrowserSnapshot } from "@/lib/controlTypes";
import styles from "./AssistantDashboard.module.css";

export default function BrowserControls({ snapshot, busy, url, onAction }: { snapshot: BrowserSnapshot | null; busy: boolean; url: string; onAction: (payload: Record<string, unknown>, label: string) => void }) {
  // Only offer element numbers from the latest inspected page, not guessed screen positions.
  const [elementId, setElementId] = useState("");
  const [text, setText] = useState("");
  const selected = snapshot?.elements.find(element => String(element.id) === elementId);
  return <div className={styles.browserControls}>
    <p>Managed browser uses an isolated Edge session, not your personal tabs or saved logins. Every click, fill, scroll and back action needs approval. Sensitive actions require manual takeover.</p>
    <div className="system-row">
      <button className="hud-btn compact" disabled={busy} onClick={() => onAction({ action: "browserOpen", url: /^https?:/i.test(url) ? url : `https://${url}` }, "Open managed browser")}>AUTOMATE URL</button>
      <button className="hud-btn compact" disabled={busy} onClick={() => onAction({ action: "browserOpen", url: "sandbox" }, "Open browser sandbox")}>TEST SANDBOX</button>
      <button className="hud-btn compact" disabled={busy} onClick={() => { setElementId(""); onAction({ action: "browserNewTab" }, "Open a new managed tab"); }}>NEW TAB</button>
      <button className="hud-btn compact" disabled={busy || !snapshot} onClick={() => { setElementId(""); onAction({ action: "browserInspect" }, "Inspect managed page"); }}>INSPECT</button>
      <button className="hud-btn compact" disabled={busy || !snapshot} onClick={() => onAction({ action: "browserClose" }, "Close managed browser")}>CLOSE SESSION</button>
    </div>
    {snapshot && <>
      <div className={styles.browserAddress}><strong>{snapshot.title}</strong><span>{snapshot.url}</span></div>
      <img className={styles.browserShot} src={snapshot.screenshot} alt="Latest managed browser screenshot" />
      <div className="system-row"><select aria-label="Browser element" value={elementId} onChange={event => setElementId(event.target.value)}><option value="">Choose element to act on…</option>{snapshot.elements.map(element => <option key={element.id} value={element.id} disabled={element.sensitive}>{element.id} · {element.tag} · {element.label}{element.sensitive ? " (manual only)" : ""}</option>)}</select><button className="hud-btn compact" disabled={busy || !selected} onClick={() => onAction({ action: "browserClick", nodeId: selected!.id }, `Click ${selected!.label}`)}>CLICK</button></div>
      <div className="system-row"><input aria-label="Browser fill text" value={text} onChange={event => setText(event.target.value)} placeholder="Non-sensitive text to fill" maxLength={2000} /><button className="hud-btn compact" disabled={busy || !selected || !["input", "textarea"].includes(selected.tag)} onClick={() => onAction({ action: "browserFill", nodeId: selected!.id, text }, `Fill ${selected!.label}`)}>FILL</button></div>
      <div className="system-row"><button className="hud-btn compact" disabled={busy} onClick={() => onAction({ action: "browserBack" }, "Browser back")}>BACK</button><button className="hud-btn compact" disabled={busy} onClick={() => onAction({ action: "browserScroll", direction: "up" }, "Scroll browser up")}>SCROLL UP</button><button className="hud-btn compact" disabled={busy} onClick={() => onAction({ action: "browserScroll", direction: "down" }, "Scroll browser down")}>SCROLL DOWN</button></div>
      <p>Voice: “browser click {selected?.id || "1"}”, “browser fill {selected?.id || "1"} with hello”, then “confirm”. Inspect again whenever the page changes.</p>
    </>}
  </div>;
}
