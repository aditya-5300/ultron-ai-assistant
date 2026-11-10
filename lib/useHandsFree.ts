"use client";
import { useEffect, useRef, useState } from "react";
import { voiceControl, isSpeechEcho } from './voiceControl';

interface RecognitionSession {
  continuous: boolean; interimResults: boolean; lang: string;
  onresult: ((event: { resultIndex: number; results: SpeechRecognitionResultList }) => void) | null;
  onerror: ((event: { error: string }) => void) | null;
  onend: (() => void) | null;
  start(): void; abort(): void;
}
export function useHandsFree({ onCommand, onInterrupt, onLevels, speaking, busy, lastSpoken }: { onCommand: (text: string) => void; onInterrupt: () => void; onLevels: (levels: number[]) => void; speaking: boolean; busy: boolean; lastSpoken: string }) {
  const [enabled, setEnabled] = useState(false);
  const [status, setStatus] = useState("Hands-free off");
  const [transcript, setTranscript] = useState("");
  const latest = useRef({ onCommand, onInterrupt, onLevels, speaking, busy, lastSpoken });
  latest.current = { onCommand, onInterrupt, onLevels, speaking, busy, lastSpoken };
  const echoUntil = useRef(0);
  useEffect(() => { if (lastSpoken) echoUntil.current = Date.now() + 2500; }, [speaking, lastSpoken]);
  useEffect(() => {
    if (!enabled) { setStatus(current => /error|unavailable|requires|denied|stopped/i.test(current) ? current : "Hands-free off"); setTranscript(""); return; }
    const speechWindow = window as typeof window & { SpeechRecognition?: new () => RecognitionSession; webkitSpeechRecognition?: new () => RecognitionSession };
    const Recognition = speechWindow.SpeechRecognition || speechWindow.webkitSpeechRecognition;
    if (!Recognition || !navigator.mediaDevices?.getUserMedia) { setStatus("Hands-free requires a browser with Web Speech recognition on localhost/HTTPS."); setEnabled(false); return; }
    let disposed = false;
    let recognition: RecognitionSession | undefined;
    let stream: MediaStream | undefined;
    let audio: AudioContext | undefined;
    let timer: ReturnType<typeof setTimeout>;
    let frame = 0;
    let followUntil = 0;
    let lastCommand = "";
    let lastTime = 0;
    let failures = 0;
    const shutdown = () => {
      // Release the microphone and all retry timers when listening is turned off or the page closes.
      if (disposed) return;
      disposed = true; clearTimeout(timer); cancelAnimationFrame(frame);
      if (recognition) { recognition.onend = null; recognition.onresult = null; recognition.onerror = null; recognition.abort(); }
      stream?.getTracks().forEach(track => track.stop()); void audio?.close();
    };
    const restart = () => {
      clearTimeout(timer);
      if (disposed) return;
      timer = setTimeout(() => {
        if (disposed) return;
        try { recognition?.start(); setStatus("Listening for ‘Ultron’ · microphone on"); }
        catch { if (++failures < 8) restart(); else { setStatus("Listener stopped after repeated failures. Toggle hands-free to retry."); setEnabled(false); } }
      }, Math.min(15000, 700 * 2 ** failures));
    };
    void (async () => {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
        if (disposed) { stream.getTracks().forEach(track => track.stop()); return; }
        audio = new AudioContext();
        const analyser = audio.createAnalyser(); analyser.fftSize = 64;
        audio.createMediaStreamSource(stream).connect(analyser);
        const samples = new Uint8Array(analyser.frequencyBinCount);
        const tick = () => {
          if (disposed) return;
          if (latest.current.speaking) echoUntil.current = Date.now() + 2500;
          if (!latest.current.speaking && !latest.current.busy) { analyser.getByteFrequencyData(samples); latest.current.onLevels(Array.from({ length: 28 }, (_, index) => Math.max(0.08, samples[index % samples.length] / 255))); }
          frame = requestAnimationFrame(tick);
        };
        tick();
        recognition = new Recognition(); recognition.continuous = true; recognition.interimResults = true; recognition.lang = "en-US";
        recognition.onresult = event => {
          if (disposed) return;
          failures = 0;
          for (let index = event.resultIndex; index < event.results.length; index++) {
            const result = event.results[index];
            const raw = result[0].transcript.trim();
            const recentOutput = latest.current.speaking || Date.now() < echoUntil.current;
            const control = voiceControl(raw, recentOutput || latest.current.busy);
            if (control) {
              // Wait for the complete phrase so "stop" is not mistaken for "stop listening".
              if (!result.isFinal) continue;
              followUntil = 0;
              if (control === 'stopListening') { shutdown(); setEnabled(false); setStatus('Listening stopped · microphone released'); return; }
              latest.current.onInterrupt(); setStatus('Interrupted · listening for Ultron'); continue;
            }
            if (recentOutput && isSpeechEcho(raw, latest.current.lastSpoken)) {
              // Speaker feedback should not become a new user request.
              setTranscript('Speaker echo ignored');
              continue;
            }
            const wake = /\b(?:hey\s+)?ultron\b[\s,.:;-]*/i.exec(raw);
            const command = (wake ? raw.slice(wake.index + wake[0].length) : raw).trim();
            setTranscript(raw);
            if (!result.isFinal) continue;
            if (wake) followUntil = Date.now() + 30000;
            if (!wake && (Date.now() > followUntil || latest.current.speaking || latest.current.busy)) continue;
            if (!command) { setStatus("Awake · speak your request"); continue; }
            if (/^(yes|confirm|yes confirm|confirm action|approve|approve this step|approve once)[.!]?$/i.test(command)) { setStatus("Approvals require the on-screen button in hands-free mode."); continue; }
            if (command === lastCommand && Date.now() - lastTime < 2500) continue;
            if (latest.current.busy && !wake) continue;
            if (latest.current.speaking || latest.current.busy) latest.current.onInterrupt();
            lastCommand = command; lastTime = Date.now(); followUntil = Date.now() + 30000;
            setStatus("Conversation active · follow-up window 30s");
            latest.current.onCommand(command);
          }
        };
        recognition.onerror = event => {
          if (disposed || event.error === "aborted" || event.error === "no-speech") return;
          if (["not-allowed", "service-not-allowed", "audio-capture"].includes(event.error)) {
            setStatus(`Microphone unavailable or denied: ${event.error}. Check permissions and retry.`); setEnabled(false);
          } else {
            failures++;
            setStatus(`Recognition error: ${event.error}. Reconnecting automatically…`);
            if (failures >= 8) { setStatus("Recognition stopped after repeated errors. Check your connection and retry."); setEnabled(false); }
          }
        };
        recognition.onend = () => {
          restart();
        };
        recognition.start(); setStatus("Listening for ‘Ultron’ · microphone on");
      } catch { if (!disposed) { setStatus("Microphone unavailable or permission denied."); setEnabled(false); } }
    })();
    return shutdown;
  }, [enabled]);
  return { enabled, setEnabled, status, transcript };
}
