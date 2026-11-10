"use client";
import { useEffect, useRef, useState } from 'react';
import styles from './AssistantDashboard.module.css';
import type { ControlReply } from '@/lib/controlTypes';
type VoiceView = { enabled: boolean; status: string; busy: boolean; queued: number; capability?: string; result?: { id: number; action: string; label: string }; audio?: { level: number | null; peak: number | null; stale: boolean; signal: string }; approval?: { token: string; label: string; expires: number }; events: Array<{ time: string; text: string }> };
function VoiceResult({ result }: { result: NonNullable<VoiceView['result']> }) {
  // Load detailed results on demand so regular microphone updates stay small.
  const [data, setData] = useState<ControlReply>();
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  async function load() {
    setLoading(true); setError('');
    try {
      const response = await fetch('/api/desktop-voice', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action: 'result', id: result.id }) });
      const reply = await response.json();
      if (!response.ok) throw new Error(reply.error || 'Result unavailable.');
      setData(reply.data);
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'Result unavailable.'); }
    finally { setLoading(false); }
  }
  return <details><summary>Latest voice result: {result.label}</summary><button disabled={loading} onClick={() => void load()}>{loading ? 'Loading…' : 'View result'}</button>{error && <p role="alert">{error}</p>}{data && <div>
    <p>{data.message}</p>
    {(data.summary || data.text || data.output) && <pre style={{ whiteSpace: 'pre-wrap', maxHeight: 320, overflow: 'auto' }}>{data.summary || data.text || data.output}</pre>}
    {data.results && <ul>{data.results.map(file => <li key={file.path}><code>{file.path}</code> · {file.size} bytes</li>)}</ul>}
    {data.devices && <ul>{data.devices.map(device => <li key={device.id}>{device.id} · {device.state} · {device.details}</li>)}</ul>}
    {data.dataUrl && <img src={data.dataUrl} alt="Latest phone screenshot requested by voice" style={{ maxWidth: '100%', maxHeight: 360 }} />}
    {data.browser && <><p>{data.browser.title || 'Blank tab'} · {data.browser.url}</p><ul>{data.browser.elements.map(element => <li key={element.id}>{element.id} · {element.label}{element.sensitive ? ' · manual only' : ''}</li>)}</ul></>}
  </div>}</details>;
}
export default function DesktopVoice({ onActive, onInterrupt, onStopListening }: { onActive: (enabled: boolean) => void; onInterrupt: () => void; onStopListening: () => void }) {
  // Display the server's desktop listener state without starting a second browser microphone.
  const [state, setState] = useState<VoiceView>();
  const [error, setError] = useState('');
  const [changing, setChanging] = useState(false);
  const callback = useRef(onActive); callback.current = onActive;
  const controls = useRef({ onInterrupt, onStopListening }); controls.current = { onInterrupt, onStopListening };
  async function request(action: string, token?: string) {
    const response = await fetch('/api/desktop-voice', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action, token }) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Desktop voice unavailable');
    setState(data); callback.current(data.enabled); setError('');
  }
  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try { if (!disposed) await request('state'); } catch (failure) { if (!disposed) setError(String(failure)); }
      if (!disposed) timer = setTimeout(poll, 4000);
    };
    void poll();
    return () => { disposed = true; clearTimeout(timer); };
  }, []);
  useEffect(() => {
    let disposed = false;
    let controller: AbortController;
    let timer: ReturnType<typeof setTimeout>;
    const connect = async () => {
      controller = new AbortController();
      try {
        const response = await fetch('/api/desktop-voice', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action: 'events' }), signal: controller.signal });
        if (!response.ok || !response.body) throw new Error('Voice interrupt connection unavailable');
        const reader = response.body.getReader();
        const decoder = new TextDecoder(); let buffer = '';
        while (!disposed) {
          const { value, done } = await reader.read(); if (done) break;
          buffer += decoder.decode(value, { stream: true });
          let boundary: number;
          while ((boundary = buffer.indexOf('\n')) >= 0) {
            const line = buffer.slice(0, boundary); buffer = buffer.slice(boundary + 1);
            if (!line.trim()) continue;
            const event = JSON.parse(line);
            if (event.kind === 'interrupt') controls.current.onInterrupt();
            if (event.kind === 'stopListening') { setState(previous => previous ? { ...previous, enabled: false, status: 'Listening stopped · microphone released' } : previous); callback.current(false); controls.current.onStopListening(); }
          }
        }
      } catch { if (!disposed) setError('Voice interrupt connection reconnecting.'); }
      if (!disposed) timer = setTimeout(() => void connect(), 1500);
    };
    void connect();
    return () => { disposed = true; clearTimeout(timer); controller?.abort(); };
  }, []);
  async function act(action: string, token?: string) {
    setChanging(true);
    try { if (action === 'enable') callback.current(true); await request(action, token); }
    catch (failure) { callback.current(state?.enabled || false); setError(String(failure)); }
    finally { setChanging(false); }
  }
return <section className={`${styles.handsFreeBar} ${styles.desktopVoicePanel}`} aria-label="Desktop background voice">
  <details className={styles.desktopVoiceDisclosure}>
    <summary aria-label="Desktop voice settings"><span>
      <strong>{state?.enabled ? '● Desktop microphone enabled' : 'Desktop wake-word voice'}</strong>
      <small>{state?.status || 'Checking desktop listener…'} · {state?.queued || 0} queued</small>
    </span><span className={styles.desktopVoiceChevron} aria-hidden="true" /></summary>
    <div className={styles.desktopVoiceBody}><div>
  <small>Windows local speech. Say “Ultron” before every command. Works outside this tab while the ULTRON server runs. Your opt-in is remembered across server restarts; no Windows autostart. Say “Ultron stop” to interrupt without disabling voice. Say “Ultron stop listening” to release the microphone. These controls work during speech; headphones help prevent echo.</small>
  {state?.capability && <small>{state.capability}</small>}
  <small>Apps, folders, browser controls, files, phone commands and research plans use the same permission-gated controllers as chat. Say “Ultron show commands” for examples. Free-text recognition may make mistakes; check the displayed target before approval.</small>
  <small>Research planning sends the recognized goal and any opted-in memory to your configured AI provider. Plans must be reviewed before tools run.</small>
  <div aria-label="Windows microphone input check"><small>Windows default recording input · no raw audio saved</small><label>Input level <meter min={0} max={100} value={state?.audio?.stale ? 0 : state?.audio?.level ?? 0} /> {state?.audio?.stale ? "No recent level reading" : `${state?.audio?.level ?? 0}/100`} · peak {state?.audio?.peak ?? "—"}</label><small>{state?.audio?.signal || "Waiting for signal diagnostics"}. Speak and watch the meter. If it stays at zero, check your microphone mute and Windows Sound → Input. A moving meter confirms sound, not correct word recognition.</small><a href="ms-settings:sound">Open Windows sound input settings ↗</a></div>
  {state?.result && <VoiceResult key={state.result.id} result={state.result} />}
  <details><summary>Desktop voice activity</summary>{state?.events.map((event, index) => <p key={`${event.time}-${index}`}>{new Date(event.time).toLocaleTimeString()} · {event.text}</p>)}</details>
</div><button disabled={changing || !state} onClick={() => void act(state?.enabled ? 'disable' : 'enable')}>{state?.enabled ? 'Disable desktop voice' : 'Enable desktop voice'}</button></div>
  </details>
  {/* Closing the settings must not hide errors or permission requests. */}
  {error && <p className={styles.desktopVoiceNotice} role="alert">{error}</p>}
  {state?.approval && <div className={styles.desktopVoiceNotice} role="alertdialog" aria-label="Desktop voice approval"><p>{state.approval.label}</p><button disabled={changing} onClick={() => void act('approve', state.approval?.token)}>Approve once</button><button disabled={changing} onClick={() => void act('decline')}>Decline</button></div>}
</section>;
}
