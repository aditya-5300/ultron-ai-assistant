import DashboardIcon from "./DashboardIcon";
import styles from "./AssistantDashboard.module.css";

export type DashboardView = "home" | "chat" | "projects" | "knowledge" | "tools" | "analytics" | "automations" | "settings" | "memory";

interface DashboardSectionsProps {
  view: DashboardView;
  onPrompt: (prompt: string) => void;
  onAttach: () => void;
  onTools: () => void;
  onTestAudio: () => void;
  audioEnabled: boolean;
  onToggleAudio: () => void;
  fileLabel: string;
  fileContext: string;
  onClearFile: () => void;
  messageCount: number;
  userMessages: number;
  activity: Array<{ id: number; label: string; detail: string }>;
  accent: string;
  onAccent: (color: string) => void;
}

export default function DashboardSections(props: DashboardSectionsProps) {
  // Keep these navigation pages separate from chat, while showing which features are still placeholders.
  if (["home", "chat", "tools", "automations", "memory"].includes(props.view)) return null;
  if (props.view === "knowledge") return <section className={styles.contentPanel}>
    <div className={styles.sectionHeading}><div><span className={styles.eyebrow}>YOUR CONTEXT</span><h1>Knowledge</h1></div><button onClick={props.onAttach}><DashboardIcon name="file" />Attach text file</button></div>
    <p className={styles.secondary}>Attach a PDF, DOCX, text or code file (up to 5 MB). Text is extracted locally; selected text is sent to your AI provider only when you send a message. PDF extraction is limited to the first 30 pages; scanned images need OCR.</p>
    {props.fileLabel ? <div className={styles.document}><DashboardIcon name="file" /><h2>{props.fileLabel}</h2><pre>{props.fileContext.slice(0, 1800)}</pre><div className={styles.inlineActions}><button onClick={() => props.onPrompt("Summarize the attached file and highlight the key points.")}>Discuss this file</button><button onClick={props.onClearFile}>Remove file</button></div></div> : <div className={styles.empty}><DashboardIcon name="knowledge" /><h2>A little context goes a long way.</h2><p>No file is attached to this session yet.</p><button onClick={props.onAttach}>Choose a document</button></div>}
    <button className={styles.textButton} onClick={props.onTools}>Search local files in Tools →</button>
  </section>;
  if (props.view === "projects") return <section className={styles.contentPanel}>
    <div className={styles.sectionHeading}><div><span className={styles.eyebrow}>BUILD SOMETHING</span><h1>Projects</h1></div></div>
    <div className={styles.empty}><DashboardIcon name="projects" /><h2>Your next idea starts here.</h2><p>Create a project brief with ULTRON, or attach an existing brief. Project storage and task tracking aren’t connected yet.</p><div className={styles.inlineActions}><button onClick={() => props.onPrompt("Help me plan a new project. First ask me about the idea, goals, and constraints.")}>Start a project brief</button><button onClick={props.onAttach}>Attach a brief</button></div></div>
  </section>;
  if (props.view === "analytics") return <section className={styles.contentPanel}>
    <span className={styles.eyebrow}>THIS SESSION ONLY</span><h1>Activity overview</h1>
    <div className={styles.metrics}><div><span>Your messages</span><strong>{props.userMessages}</strong></div><div><span>Assistant replies</span><strong>{props.messageCount}</strong></div><div><span>Attached files</span><strong>{props.fileLabel ? 1 : 0}</strong></div></div>
    <h2>Recent activity</h2><p className={styles.secondary}>Live session events, not historical usage or billing data.</p><div className={styles.eventList}>{props.activity.map(event => <div key={event.id}><span>{event.label}</span><p>{event.detail}</p></div>)}</div>
  </section>;
  return <section className={styles.contentPanel}>
    <span className={styles.eyebrow}>MAKE IT YOURS</span><h1>Settings</h1>
    <div className={styles.settingRow}><div><h2>Appearance</h2><p>Electric-blue glass with gold 3D rings.</p></div><span className={styles.badge}>Dark</span></div>
    <div className={styles.settingRow}><div><h2>Accent color</h2><p>Interface highlights; the orb keeps its gold finish.</p></div><div className={styles.swatches}>{[{ color: "#25baff", label: "Blue" }, { color: "#47dfcf", label: "Teal" }, { color: "#a394ff", label: "Violet" }].map(item => <button key={item.color} style={{ background: item.color }} aria-label={`${item.label} accent`} aria-pressed={props.accent === item.color} onClick={() => props.onAccent(item.color)} />)}</div></div>
    <div className={styles.settingRow}><div><h2>Voice output</h2><p>Read assistant replies using browser speech.</p></div><button aria-pressed={props.audioEnabled} onClick={props.onToggleAudio}>{props.audioEnabled ? "Enabled" : "Muted"}</button></div>
    <div className={styles.settingRow}><div><h2>Audio test</h2><p>Check your speakers before starting a conversation.</p></div><button onClick={props.onTestAudio}>Test voice</button></div>
    <div className={styles.settingRow}><div><h2>Local demo account</h2><p>No real account, authentication, or cloud session storage is connected.</p></div><a href="/">Back to welcome</a></div>
  </section>;
}
