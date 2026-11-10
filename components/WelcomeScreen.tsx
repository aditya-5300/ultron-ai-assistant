"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { createOrbScene, type OrbSceneApi } from "@/lib/orbScene";
import { HandTracker } from "@/lib/handTracker";
import styles from "./WelcomeScreen.module.css";

const DEMO_EMAIL = "demo@ultron.local";
// These public demo details demonstrate the screen; they do not provide real account security.
const DEMO_PASSWORD = "UltronDemo123!";

export default function WelcomeScreen() {
  const router = useRouter();
  const stageRef = useRef<HTMLDivElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const overlayRef = useRef<HTMLCanvasElement>(null);
  const sceneRef = useRef<OrbSceneApi | null>(null);
  const trackerRef = useRef<HandTracker | null>(null);
  const [ready, setReady] = useState(false);
  const [gestures, setGestures] = useState<"off" | "starting" | "on">("off");
  const [visiblePassword, setVisiblePassword] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loginError, setLoginError] = useState("");
  const [notice, setNotice] = useState("");
  const [sceneError, setSceneError] = useState("");

  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      try {
        if (!stageRef.current) return;
        sceneRef.current = createOrbScene(stageRef.current, true);
        setReady(true);
      } catch {
        setSceneError("3D rendering could not start. Enable browser graphics acceleration and reload.");
      }
    });
    return () => {
      cancelAnimationFrame(frame);
      trackerRef.current?.stop();
      trackerRef.current = null;
      sceneRef.current?.dispose();
      sceneRef.current = null;
    };
  }, []);

  async function toggleGestures() {
    if (trackerRef.current) {
      trackerRef.current.stop();
      trackerRef.current = null;
      setGestures("off");
      return;
    }
    if (!videoRef.current || !overlayRef.current) return;
    setGestures("starting");
    const tracker = new HandTracker(videoRef.current, overlayRef.current, {
      onRotate: (horizontal, vertical) => sceneRef.current?.rotateBy(horizontal, vertical),
      onZoom: (factor) => sceneRef.current?.zoomBy(factor),
      onStatus: () => {},
    });
    trackerRef.current = tracker;
    try {
      await tracker.start();
      if (trackerRef.current !== tracker) { tracker.stop(); return; }
      setGestures("on");
    } catch {
      tracker.stop();
      if (trackerRef.current !== tracker) return;
      trackerRef.current = null;
      setGestures("off");
      setNotice("Camera unavailable. Allow camera access to enable gestures, or drag the orb to rotate.");
    }
  }

  function explainAuth() {
    setNotice("Real accounts and social sign-in are not connected. Use the demo credentials below; no credentials are sent or saved.");
  }

  return (
    <main className={styles.page}>
      <div className={styles.frame}>
        <header className={styles.brand}><span>ULTRON</span><small>YOUR AI ASSISTANT</small></header>
        <section className={styles.visual} aria-label="Interactive holographic orb">
          <div className={styles.stars} aria-hidden="true" />
          <div ref={stageRef} className={styles.stage} aria-label="Drag to rotate the 3D orb. Scroll to zoom." />
          {!ready && <div className={styles.loading} role="status">{sceneError || "Initializing holographic core…"}</div>}
          <div className={styles.orbControls}>
            <button type="button" onClick={() => void toggleGestures()} disabled={gestures === "starting" || !ready}>{gestures === "on" ? "◉ Gestures on" : gestures === "starting" ? "Connecting camera…" : "◎ Enable gestures"}</button>
            <button type="button" onClick={() => sceneRef.current?.resetView()} disabled={!ready}>Reset view</button>
          </div>
          <div className={styles.caption}><span>THINK</span><span>ANALYZE</span><span>SOLVE</span><span>CREATE</span><span>EVOLVE</span></div>
          <p className={styles.hint}>Drag to rotate · Pinch both hands, then spread to zoom in or bring together to zoom out</p>
        </section>
        <section className={styles.formArea} aria-labelledby="welcome-title">
          <div className={styles.card}>
            <h1 id="welcome-title">Welcome to ULTRON</h1>
            <p className={styles.intro}>Your intelligent companion for a<br /> smarter tomorrow.</p>
            <form onSubmit={(event) => {
              event.preventDefault();
              if (email.trim().toLowerCase() !== DEMO_EMAIL || password !== DEMO_PASSWORD) {
                setLoginError("Use the demo email and password shown below, or select Fill demo credentials.");
                return;
              }
              setLoginError("");
              setPassword("");
              router.push("/assistant");
            }}>
              <label className={styles.field}><span aria-hidden="true">✉</span><input type="email" aria-label="Email address" placeholder="Email address" autoComplete="off" value={email} required onChange={(event) => { setEmail(event.target.value); setLoginError(""); }} /></label>
              <label className={styles.field}><span aria-hidden="true">♙</span><input type={visiblePassword ? "text" : "password"} aria-label="Password" placeholder="Password" autoComplete="off" value={password} required onChange={(event) => { setPassword(event.target.value); setLoginError(""); }} /><button type="button" className={styles.eye} aria-label={visiblePassword ? "Hide password" : "Show password"} aria-pressed={visiblePassword} onClick={() => setVisiblePassword(!visiblePassword)}>◉</button></label>
              <button className={styles.forgot} type="button" onClick={explainAuth}>Forgot password?</button>
              <button className={styles.signIn} type="submit">Sign in <span aria-hidden="true">→</span></button>
            </form>
            {loginError && <p role="alert" className={styles.loginError}>{loginError}</p>}
            <div className={styles.demo}>
              <strong>Demo access only</strong>
              <span>Email: <code>{DEMO_EMAIL}</code></span>
              <span>Password: <code>{DEMO_PASSWORD}</code></span>
              <button type="button" onClick={() => { setEmail(DEMO_EMAIL); setPassword(DEMO_PASSWORD); setLoginError(""); }}>Fill demo credentials</button>
              <small>Local preview, not secure authentication. Don’t use a real password.</small>
            </div>
            <div className={styles.divider}><span>or continue with</span></div>
            <div className={styles.providers}>
              <button type="button" onClick={explainAuth}><b>G</b> Google</button>
              <button type="button" onClick={explainAuth}><b>◈</b> GitHub</button>
              <button type="button" onClick={explainAuth}><i className={styles.microsoft} aria-hidden="true" /> Microsoft</button>
            </div>
            <p className={styles.signup}>New to ULTRON? <button type="button" onClick={explainAuth}>Create an account</button></p>
            <div className={styles.local}><span>LOCAL PREVIEW · NO ACCOUNT REQUIRED</span><Link href="/assistant">Open local assistant <span aria-hidden="true">↗</span></Link></div>
            {notice && <p role="status" className={styles.notice}>{notice}</p>}
          </div>
        </section>
        <video ref={videoRef} className={styles.capture} muted playsInline aria-hidden="true" />
        <canvas ref={overlayRef} className={styles.capture} aria-hidden="true" />
      </div>
    </main>
  );
}
