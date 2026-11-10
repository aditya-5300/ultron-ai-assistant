"use client";

import { FormEvent, type CSSProperties, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { createOrbScene, type OrbSceneApi } from "@/lib/orbScene";
import { HandTracker, type TrackerStatus } from "@/lib/handTracker";
import { isAgentCommand, isSystemCommand, normalizeCommand, parseVoicePlan, type VoiceCommand } from "@/lib/voiceCommands";
import type { BrowserSnapshot, ControlReply } from "@/lib/controlTypes";
import BrowserControls from "./BrowserControls";
import AgentWorkspace from "./AgentWorkspace";
import MemoryWorkspace from "./MemoryWorkspace";
import { useConversationMemory } from "@/lib/useConversationMemory";
import { useHandsFree } from "@/lib/useHandsFree";
import DesktopVoice from './DesktopVoice';
import { voiceControl } from '@/lib/voiceControl';
import DashboardIcon from "./DashboardIcon";
import DashboardSections, { type DashboardView } from "./DashboardSections";
import styles from "./AssistantDashboard.module.css";

type CameraState = "off" | "starting" | "on" | "error";
type AssistantMode = "idle" | "listening" | "thinking" | "speaking";
type ChatRole = "user" | "assistant";

interface ChatMessage {
  id: number;
  role: ChatRole;
  content: string;
}

interface ActivityLog {
  id: number;
  label: string;
  detail: string;
}

interface SpeechRecognitionEventLike extends Event {
  resultIndex: number;
  results: SpeechRecognitionResultList;
}

interface SpeechRecognitionErrorEventLike extends Event {
  error: string;
}

interface SpeechRecognitionLike extends EventTarget {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onerror: ((event: SpeechRecognitionErrorEventLike) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
}

type SpeechRecognitionConstructor = new () => SpeechRecognitionLike;

const MODE_LABEL: Record<TrackerStatus["mode"], string> = {
  idle: "STANDBY",
  spin: "SPIN",
  zoom: "ZOOM",
};

const ASSISTANT_COPY: Record<AssistantMode, { label: string; subtitle: string }> = {
  idle: {
    label: "STANDBY",
    subtitle: "Speak, type, or attach a file to get started.",
  },
  listening: {
    label: "LISTENING",
    subtitle: "Voice capture active. Say a command and ULTRON will respond.",
  },
  thinking: {
    label: "ANALYZING",
    subtitle: "AI stream active. Response tokens are inbound.",
  },
  speaking: {
    label: "RESPONDING",
    subtitle: "Voice output active. The orb follows assistant state.",
  },
};

const WAVEFORM_BARS = Array.from({ length: 28 }, (_, index) => index);
const EMPTY_LEVELS = WAVEFORM_BARS.map(() => 0.08);

const INITIAL_MESSAGES: ChatMessage[] = [
  {
    id: 1,
    role: "assistant",
    content: "Welcome to ULTRON. Ask me a question, attach a file, or try a local command like ‘open Notepad’. AI chat needs your configured provider key; local commands don’t.",
  },
];

function getSpeechRecognition(): SpeechRecognitionConstructor | null {
  if (typeof window === "undefined") return null;
  const speechWindow = window as typeof window & {
    SpeechRecognition?: SpeechRecognitionConstructor;
    webkitSpeechRecognition?: SpeechRecognitionConstructor;
  };
  return speechWindow.SpeechRecognition || speechWindow.webkitSpeechRecognition || null;
}

function streamLevels(seed: number) {
  return WAVEFORM_BARS.map((index) => {
    const wave = Math.sin((seed + index) * 0.72);
    return 0.18 + Math.abs(wave) * 0.72;
  });
}

export default function JarvisOrb() {
  // This screen connects chat, voice, the 3D orb and the same local tools used by agent tasks.
  const [desktopVoiceActive, setDesktopVoiceActive] = useState(false);
  const [view, setView] = useState<DashboardView>("home");
  const [accent, setAccent] = useState("#25baff");
  const [searchQuery, setSearchQuery] = useState("");
  const commandInputRef = useRef<HTMLTextAreaElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const chatPanelRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const overlayRef = useRef<HTMLCanvasElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const sceneRef = useRef<OrbSceneApi | null>(null);
  const trackerRef = useRef<HandTracker | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const micStreamRef = useRef<MediaStream | null>(null);
  const waveformRafRef = useRef(0);
  const speechRecognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);
  const speakingTimerRef = useRef<number | null>(null);
  const logIdRef = useRef(4);
  const messageIdRef = useRef(2);
  const pendingCommandRef = useRef<{ command: VoiceCommand; expires: number; remaining: VoiceCommand[] } | null>(null);
  const browserSnapshotRef = useRef<BrowserSnapshot | null>(null);
  const selectedDeviceRef = useRef("");
  const controlBusyRef = useRef(false);
  const [controlBusy, setControlBusy] = useState(false);
  const [approval, setApproval] = useState<ControlReply["approval"]>();
  const [browserSnapshot, setBrowserSnapshot] = useState<BrowserSnapshot | null>(null);
  const [phoneDevices, setPhoneDevices] = useState<NonNullable<ControlReply["devices"]>>([]);
  const [selectedDevice, setSelectedDevice] = useState("");
  const [controlAudit, setControlAudit] = useState<NonNullable<ControlReply["events"]>>([]);

  const [camera, setCamera] = useState<CameraState>("off");
  const [status, setStatus] = useState<TrackerStatus>({ hands: 0, mode: "idle" });
  const [assistantMode, setAssistantMode] = useState<AssistantMode>("idle");
  const [voiceActive, setVoiceActive] = useState(false);
  const [speechSupported, setSpeechSupported] = useState(true);
  const webSearch = false;
  const [speechOutput, setSpeechOutput] = useState(true);
  const [audioLevels, setAudioLevels] = useState(EMPTY_LEVELS);
  const [commandText, setCommandText] = useState("");
  const [interimTranscript, setInterimTranscript] = useState("");
  const [fileContext, setFileContext] = useState("");
  const [fileLabel, setFileLabel] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>(INITIAL_MESSAGES);
  const [activityLog, setActivityLog] = useState<ActivityLog[]>([
    { id: 1, label: "BOOT", detail: "Dashboard session opened" },
    { id: 2, label: "AI", detail: "Chat uses the server-configured AI provider" },
    { id: 3, label: "TOOLS", detail: "Local commands and file context available" },
  ]);
  const [error, setError] = useState<string | null>(null);
  const [desktopApp, setDesktopApp] = useState("notepad");
  const [browserUrl, setBrowserUrl] = useState("https://www.google.com");
  const [fileSearch, setFileSearch] = useState("");
  const [fileRoot, setFileRoot] = useState("documents");
  const [systemResult, setSystemResult] = useState("");
  const [fileHits, setFileHits] = useState<Array<{ name: string; path: string; size: number }>>([]);
  const [adbPackage, setAdbPackage] = useState("com.android.settings");
  const [adbTextInput, setAdbTextInput] = useState("");
  const [adbTapX, setAdbTapX] = useState("500");
  const [adbTapY, setAdbTapY] = useState("1000");
  const [adbSwipe, setAdbSwipe] = useState({ x1: "500", y1: "1400", x2: "500", y2: "500" });
  const [adbScreenshot, setAdbScreenshot] = useState("");
  const [agentGoal, setAgentGoal] = useState({ text: "", key: 0 });
  const [agentBusy, setAgentBusy] = useState(false);
  const [agentStop, setAgentStop] = useState(0);
  const lastSpokenRef = useRef("");
  const handsFreeActiveRef = useRef(false);
  const memory = useConversationMemory(messages, !!abortControllerRef.current, (saved) => {
    messageIdRef.current = Math.max(1, ...saved.map(message => message.id)) + 1;
    setMessages(saved);
  });
  const userName = memory.state?.preferences.name || "Aditya";

  const addLog = useCallback((label: string, detail: string) => {
    setActivityLog((current) => [
      { id: logIdRef.current++, label, detail },
      ...current,
    ].slice(0, 7));
  }, []);

  const performControl = useCallback(async (payload: Record<string, unknown>, label: string, signal?: AbortSignal) => {
    if (controlBusyRef.current) throw new Error("Another local action is running. Wait for its result.");
    const action = String(payload.action);
    const effectivePayload = { ...payload };
    if (action.startsWith("adb") && !effectivePayload.deviceId && selectedDeviceRef.current) effectivePayload.deviceId = selectedDeviceRef.current;
    if (action.startsWith("browser") && !["browserOpen", "browserNewTab", "browserInspect", "browserClose"].includes(action) && !effectivePayload.snapshot) effectivePayload.snapshot = browserSnapshotRef.current?.snapshot || "";
    controlBusyRef.current = true;
    setControlBusy(true);
    addLog("SYS", label);
    try {
      const response = await fetch("/api/system-control", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(effectivePayload), signal });
      const data = await response.json() as ControlReply;
      if (response.status !== 202 && (!response.ok || !data.ok)) throw new Error(data.error || "System control failed.");
      if (data.browser) { browserSnapshotRef.current = data.browser; setBrowserSnapshot(data.browser); }
      if (data.browserClosed) { browserSnapshotRef.current = null; setBrowserSnapshot(null); }
      if (data.devices) setPhoneDevices(data.devices);
      if (data.deviceId) { selectedDeviceRef.current = data.deviceId; setSelectedDevice(data.deviceId); }
      if (data.events) setControlAudit(data.events);
      if (data.results) setFileHits(data.results);
      if (data.dataUrl) setAdbScreenshot(data.dataUrl);
      if (data.text) { setFileContext(data.text); setFileLabel(data.filePath || "Selected document"); }
      return { data, effectivePayload };
    } finally { controlBusyRef.current = false; setControlBusy(false); }
  }, [addLog]);

  const queueApproval = useCallback((command: VoiceCommand, nextApproval: NonNullable<ControlReply["approval"]>, remaining: VoiceCommand[] = []) => {
    pendingCommandRef.current = { command: { ...command, payload: { ...command.payload, approvalToken: nextApproval.token } }, expires: nextApproval.expires, remaining };
    setApproval(nextApproval);
    return `Permission required: ${nextApproval.label} Say confirm or use Approve once within 60 seconds. Say cancel to decline.${remaining.length ? ` ${remaining.length} later step(s) are paused.` : ""}`;
  }, []);

  function controlReplyText(data: ControlReply) {
    if (data.summary) return `Local extractive summary${data.truncated ? " (limited document text)" : ""}: ${data.summary}`;
    if (data.results) return `Found ${data.results.length} file(s). ${data.results.slice(0, 5).map(hit => hit.name).join(", ")}. See Tools for the file results.`;
    if (data.dataUrl) return "Phone screenshot captured. See Tools for the screen image.";
    return data.message || data.output || "Local action completed.";
  }

  useEffect(() => {
    if (!approval) return;
    const timer = window.setTimeout(() => { pendingCommandRef.current = null; setApproval(undefined); setSystemResult("Approval expired. Request the action again."); }, Math.max(0, approval.expires - Date.now()));
    return () => window.clearTimeout(timer);
  }, [approval]);

  const clearSpeakingTimer = useCallback(() => {
    if (speakingTimerRef.current !== null) {
      window.clearInterval(speakingTimerRef.current);
      speakingTimerRef.current = null;
    }
  }, []);

  const stopSpeechOutput = useCallback(() => {
    clearSpeakingTimer();
    window.speechSynthesis?.cancel();
  }, [clearSpeakingTimer]);

  const speakText = useCallback(
    (text: string) => {
      if (!speechOutput || !("speechSynthesis" in window) || !text.trim()) {
        setAssistantMode("idle");
        return;
      }
      stopSpeechOutput();
      const utterance = new SpeechSynthesisUtterance(text.slice(0, 900));
      lastSpokenRef.current = text.slice(0, 900);
      utterance.rate = 0.95;
      utterance.pitch = 0.82;
      utterance.volume = 0.95;
      utterance.onstart = () => {
        let tick = 0;
        setAssistantMode("speaking");
        speakingTimerRef.current = window.setInterval(() => {
          tick += 1;
          setAudioLevels(streamLevels(tick));
        }, 90);
      };
      utterance.onend = () => {
        clearSpeakingTimer();
        setAudioLevels(EMPTY_LEVELS);
        setAssistantMode("idle");
      };
      utterance.onerror = () => {
        clearSpeakingTimer();
        setAudioLevels(EMPTY_LEVELS);
        setAssistantMode("idle");
      };
      window.speechSynthesis.speak(utterance);
    },
    [clearSpeakingTimer, speechOutput, stopSpeechOutput],
  );

  const stopVoice = useCallback((nextMode: AssistantMode = "idle") => {
    cancelAnimationFrame(waveformRafRef.current);
    speechRecognitionRef.current?.stop();
    speechRecognitionRef.current = null;
    analyserRef.current = null;
    micStreamRef.current?.getTracks().forEach((track) => track.stop());
    micStreamRef.current = null;
    void audioContextRef.current?.close();
    audioContextRef.current = null;
    setVoiceActive(false);
    setInterimTranscript("");
    setAudioLevels(EMPTY_LEVELS);
    setAssistantMode(nextMode);
  }, []);

  const cancelAssistant = useCallback(() => {
    pendingCommandRef.current = null;
    setApproval(undefined);
    abortControllerRef.current?.abort();
    abortControllerRef.current = null;
    stopSpeechOutput();
    setAssistantMode("idle");
    setAudioLevels(EMPTY_LEVELS);
    addLog("AI", "Current response cancelled");
  }, [addLog, stopSpeechOutput]);

  const sendToAssistant = useCallback(
    async (content: string, handsFreeInput = false) => {
      // Handle known local commands before contacting AI, so basic controls do not need API credit.
      const prompt = content.trim();
      if (!prompt || abortControllerRef.current) return;
      if (handsFreeInput && /^(yes|confirm|yes confirm|confirm action)$/.test(normalizeCommand(prompt).toLowerCase())) {
        setError("Use the on-screen approval button. Hands-free audio cannot approve actions.");
        return;
      }
      if (isAgentCommand(prompt)) {
        setAgentGoal({ text: prompt, key: Date.now() });
        setView("automations");
        setCommandText("");
        speakText("I will draft a task plan. Review it in Agent tasks before anything runs.");
        return;
      }
      setView("chat");

      stopVoice("thinking");
      stopSpeechOutput();
      setError(null);
      setCommandText("");
      setInterimTranscript("");

      const userMessage: ChatMessage = {
        id: messageIdRef.current++,
        role: "user",
        content: prompt,
      };
      const assistantMessage: ChatMessage = {
        id: messageIdRef.current++,
        role: "assistant",
        content: "",
      };
      const nextMessages = [...messages, userMessage, assistantMessage];
      setMessages(nextMessages);
      addLog("AI", webSearch ? "Streaming with web search enabled" : "Streaming assistant response");

      const controller = new AbortController();
      abortControllerRef.current = controller;
      let assistantText = "";

      try {
        const normalized = normalizeCommand(prompt).toLowerCase();
        const pending = pendingCommandRef.current;
        pendingCommandRef.current = null;
        setApproval(undefined);
        let plan = parseVoicePlan(prompt);
        let localReply = "";
        if (/^(yes|confirm|yes confirm|confirm action)$/.test(normalized)) {
          if (pending && Date.now() < pending.expires) {
            plan = [pending.command, ...pending.remaining];
          } else localReply = "No pending command to confirm, or confirmation expired. Please repeat the command.";
        } else if (/^(no|cancel|cancel action)$/.test(normalized)) {
          localReply = "Cancelled. No new system action was performed.";
        }
        if (plan && !localReply) {
          const completed: string[] = [];
          for (let step = 0; step < plan.length; step++) {
            const command = plan[step];
            try {
              const { data, effectivePayload } = await performControl(command.payload, command.label, controller.signal);
              if (abortControllerRef.current !== controller) return;
              if (data.approval) { completed.push(queueApproval({ ...command, payload: effectivePayload }, data.approval, plan.slice(step + 1))); break; }
              completed.push(controlReplyText(data));
            } catch (failure) {
              completed.push(`Stopped at step ${step + 1}: ${failure instanceof Error ? failure.message : "Action failed"}. Later steps were not run.`);
              break;
            }
          }
          localReply = completed.join("\n\n");
          setSystemResult(localReply);
        } else if (!localReply && isSystemCommand(prompt)) {
          localReply = "That command is not supported or needs clearer wording. Try open Notepad, open new tab, find files named report in documents, browser open youtube, browser click 1, or phone home. Join up to six supported actions with then. No action was performed. Arbitrary shell commands are not supported.";
        }
        if (localReply) {
          setMessages((current) => current.map((message) => message.id === assistantMessage.id ? { ...message, content: localReply } : message));
          abortControllerRef.current = null;
          setAudioLevels(EMPTY_LEVELS);
          addLog("SYS", localReply.slice(0, 100));
          speakText(localReply);
          return;
        }
        const response = await fetch("/api/ultron", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            messages: nextMessages
              .filter((message) => message.content.trim())
              .map(({ role, content }) => ({ role, content })),
            fileContext,
            webSearch,
          }),
          signal: controller.signal,
        });

        if (!response.ok || !response.body) {
          const detail = await response.text();
          let message = "Assistant request failed";
          try { const parsed = JSON.parse(detail); if (typeof parsed.error === 'string') message = parsed.error; } catch {}
          throw new Error(message);
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let pulse = 0;

        while (true) {
          const { value, done } = await reader.read();
          if (done) break;

          const chunk = decoder.decode(value, { stream: true });
          assistantText += chunk;
          pulse += 1;
          setAudioLevels(streamLevels(pulse));
          setMessages((current) =>
            current.map((message) =>
              message.id === assistantMessage.id ? { ...message, content: assistantText } : message,
            ),
          );
        }

        if (abortControllerRef.current !== controller) return;
        abortControllerRef.current = null;
        setAudioLevels(EMPTY_LEVELS);
        addLog("AI", "Assistant response complete");
        if (assistantText.trim()) speakText(assistantText);
        else setAssistantMode("idle");
      } catch (err) {
        if (abortControllerRef.current !== controller) return;
        abortControllerRef.current = null;
        setAssistantMode("idle");
        setAudioLevels(EMPTY_LEVELS);

        if (err instanceof DOMException && err.name === "AbortError") return;

        const message = err instanceof Error ? err.message : "Assistant request failed";
        setError(
          message.includes("GEMINI_API_KEY")
            ? "GEMINI_API_KEY MISSING"
            : message.includes("OPENAI_API_KEY")
              ? "OPENAI_API_KEY MISSING"
              : "AI REQUEST FAILED",
        );
        addLog("ERROR", message.slice(0, 90));
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessage.id
              ? { ...item, content: `Unable to connect: ${message}` }
              : item,
          ),
        );
        speakText(`The request failed. ${message.slice(0, 250)}`);
      }
    },
    [addLog, fileContext, messages, performControl, queueApproval, speakText, stopSpeechOutput, stopVoice, webSearch],
  );

  useEffect(() => {
    setSpeechSupported(!!getSpeechRecognition());
  }, []);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    let scene: OrbSceneApi | undefined;
    const initFrame = requestAnimationFrame(() => {
      try {
        scene = createOrbScene(container, "dashboard");
        sceneRef.current = scene;
      } catch (err) {
        setError(err instanceof Error ? `SCENE INIT FAILED: ${err.message}` : "SCENE INIT FAILED");
      }
    });
    return () => {
      cancelAnimationFrame(initFrame);
      trackerRef.current?.stop();
      trackerRef.current = null;
      stopVoice();
      cancelAssistant();
      scene?.dispose();
      sceneRef.current = null;
    };
  }, [cancelAssistant, stopVoice]);

  const stopGestures = useCallback(() => {
    trackerRef.current?.stop();
    trackerRef.current = null;
    setCamera("off");
    setStatus({ hands: 0, mode: "idle" });
    addLog("GESTURE", "Hand tracking disabled");
  }, [addLog]);

  const startGestures = useCallback(async () => {
    const video = videoRef.current;
    const overlay = overlayRef.current;
    if (!video || !overlay || trackerRef.current) return;

    setCamera("starting");
    setError(null);

    const tracker = new HandTracker(video, overlay, {
      onRotate: (dt, dp) => sceneRef.current?.rotateBy(dt, dp),
      onZoom: (factor) => sceneRef.current?.zoomBy(factor),
      onStatus: setStatus,
    });
    trackerRef.current = tracker;

    try {
      await tracker.start();
      setCamera("on");
      addLog("GESTURE", "Hand tracking enabled");
    } catch (err) {
      trackerRef.current = null;
      tracker.stop();
      setCamera("error");
      setError(
        err instanceof DOMException && err.name === "NotAllowedError"
          ? "CAMERA ACCESS DENIED"
          : "TRACKING INIT FAILED",
      );
      addLog("ERROR", "Gesture camera failed to initialize");
    }
  }, [addLog]);

  const toggleGestures = useCallback(() => {
    if (trackerRef.current) stopGestures();
    else void startGestures();
  }, [startGestures, stopGestures]);

  const startListening = useCallback(async () => {
    if (voiceActive || handsFreeActiveRef.current || desktopVoiceActive) return;
    stopSpeechOutput();
    setError(null);

    const Recognition = getSpeechRecognition();
    if (!Recognition) {
      setSpeechSupported(false);
      setError("SPEECH RECOGNITION UNAVAILABLE");
      addLog("VOICE", "Speech recognition is not supported in this browser");
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
        video: false,
      });
      const AudioContextCtor =
        window.AudioContext ||
        (window as typeof window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;

      if (!AudioContextCtor) throw new Error("AudioContext unavailable");

      const audioContext = new AudioContextCtor();
      const analyser = audioContext.createAnalyser();
      analyser.fftSize = 64;
      analyser.smoothingTimeConstant = 0.74;
      audioContext.createMediaStreamSource(stream).connect(analyser);

      const recognition = new Recognition();
      recognition.continuous = false;
      recognition.interimResults = true;
      recognition.lang = "en-US";
      recognition.onresult = (event) => {
        let finalTranscript = "";
        let interim = "";
        for (let index = event.resultIndex; index < event.results.length; index += 1) {
          const result = event.results[index];
          if (result.isFinal) finalTranscript += result[0].transcript;
          else interim += result[0].transcript;
        }
        setInterimTranscript(interim);
        if (finalTranscript.trim()) {
          const control = voiceControl(finalTranscript, true);
          if (control === 'interrupt') { cancelAssistant(); stopVoice(); setAgentStop(value => value + 1); return; }
          if (control === 'stopListening') { stopVoice(); return; }
          void sendToAssistant(finalTranscript);
        }
      };
      recognition.onerror = (event) => {
        setError(`VOICE ERROR: ${event.error.toUpperCase()}`);
        addLog("VOICE", `Speech recognition error: ${event.error}`);
        stopVoice();
      };
      recognition.onend = () => {
        if (speechRecognitionRef.current === recognition) stopVoice();
      };

      audioContextRef.current = audioContext;
      analyserRef.current = analyser;
      micStreamRef.current = stream;
      speechRecognitionRef.current = recognition;
      setVoiceActive(true);
      setAssistantMode("listening");
      addLog("VOICE", "Speech recognition started");

      const data = new Uint8Array(analyser.frequencyBinCount);
      const tick = () => {
        analyser.getByteFrequencyData(data);
        setAudioLevels(
          WAVEFORM_BARS.map((_, index) => {
            const value = data[index % data.length] / 255;
            return Math.max(0.08, value);
          }),
        );
        waveformRafRef.current = requestAnimationFrame(tick);
      };
      tick();
      recognition.start();
    } catch (err) {
      setVoiceActive(false);
      setAssistantMode("idle");
      setError(
        err instanceof DOMException && err.name === "NotAllowedError"
          ? "MICROPHONE ACCESS DENIED"
          : "VOICE INIT FAILED",
      );
      addLog("ERROR", "Voice listener failed");
    }
  }, [addLog, sendToAssistant, stopSpeechOutput, stopVoice, voiceActive, desktopVoiceActive, cancelAssistant]);

  const toggleVoice = useCallback(() => {
    if (voiceActive) {
      stopVoice();
      addLog("VOICE", "Voice listener stopped");
    } else {
      void startListening();
    }
  }, [addLog, startListening, stopVoice, voiceActive]);

  const handsFree = useHandsFree({
    speaking: assistantMode === "speaking", busy: !!abortControllerRef.current || agentBusy,
    lastSpoken: lastSpokenRef.current,
    onLevels: setAudioLevels,
    onInterrupt: () => { cancelAssistant(); setAgentStop(value => value + 1); },
    onCommand: (text) => { void sendToAssistant(text, true); },
  });
  handsFreeActiveRef.current = handsFree.enabled || desktopVoiceActive;
  useEffect(() => {
    if (desktopVoiceActive) { handsFree.setEnabled(false); stopVoice(); }
  }, [desktopVoiceActive, handsFree.setEnabled, stopVoice]);

  const resetInterface = useCallback(() => {
    cancelAssistant();
    stopVoice();
    sceneRef.current?.resetView();
    addLog("SYSTEM", "View and assistant state reset");
  }, [addLog, cancelAssistant, stopVoice]);

  const handleSubmit = useCallback(
    (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      void sendToAssistant(commandText);
    },
    [commandText, sendToAssistant],
  );

  const loadFileContext = useCallback(async (file: File) => {
    try {
      if (file.size > 5 * 1024 * 1024) throw new Error("Choose a file smaller than 5 MB.");
      const body = new FormData();
      body.set("file", file);
      const response = await fetch("/api/documents", { method: "POST", body });
      const data = await response.json() as ControlReply;
      if (!response.ok || !data.ok || !data.text) throw new Error(data.error || "Document reading failed.");
      setFileLabel(`${file.name} (${Math.round(file.size / 1024)} KB)${data.truncated ? " · extraction limited" : ""}`);
      setFileContext(data.text);
      addLog("FILE", `Loaded context from ${file.name}`);
      setSystemResult(controlReplyText(data));
    } catch (failure) { setError(failure instanceof Error ? failure.message : "Document reading failed."); }
  }, [addLog]);

  const callSystem = useCallback(
    async (payload: Record<string, unknown>, label: string) => {
      setSystemResult("Running...");
      pendingCommandRef.current = null;
      setApproval(undefined);

      try {
        const { data, effectivePayload } = await performControl(payload, label);
        if (data.approval) setSystemResult(queueApproval({ payload: effectivePayload, label }, data.approval));
        else setSystemResult(controlReplyText(data));
      } catch (err) {
        const message = err instanceof Error ? err.message : "System action failed.";
        setSystemResult(message);
        addLog("ERROR", message.slice(0, 90));
      }
    },
    [addLog, performControl, queueApproval],
  );

  const openBrowserUrl = useCallback(() => {
    const trimmed = browserUrl.trim();
    if (!trimmed) return;
    const url = /^https?:\/\//i.test(trimmed) ? trimmed : `https://${trimmed}`;
    void callSystem({ action: "openWebsite", url }, `Open ${url}`);
  }, [browserUrl, callSystem]);

  useEffect(() => {
    const panel = chatPanelRef.current;
    if (panel) panel.scrollTop = panel.scrollHeight;
  }, [messages, view]);

  useEffect(() => {
    function focusSearch(event: KeyboardEvent) {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        searchInputRef.current?.focus();
      }
    }
    window.addEventListener("keydown", focusSearch);
    return () => window.removeEventListener("keydown", focusSearch);
  }, []);

  function preparePrompt(prompt: string) {
    setView("chat");
    setCommandText(prompt);
    requestAnimationFrame(() => commandInputRef.current?.focus());
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const isTyping = target?.tagName === "INPUT" || target?.tagName === "TEXTAREA";
      if (isTyping) return;

      switch (e.key) {
        case "+":
        case "=":
          sceneRef.current?.zoomIn();
          break;
        case "-":
        case "_":
          sceneRef.current?.zoomOut();
          break;
        case "r":
        case "R":
          resetInterface();
          break;
        case "g":
        case "G":
          toggleGestures();
          break;
        case "v":
        case "V":
          toggleVoice();
          break;
        case "Escape":
          cancelAssistant();
          setAgentStop(value => value + 1);
          break;
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [cancelAssistant, resetInterface, toggleGestures, toggleVoice]);

  const cameraOn = camera === "on";
  const assistant = ASSISTANT_COPY[assistantMode];
  const isStreaming = !!abortControllerRef.current;

  return (
    <div className={styles.dashboard} data-view={view} style={{ "--dash-accent": accent } as CSSProperties}>
      <aside className={styles.sidebar}>
        <Link href="/" className={styles.brand}>ULTRON<small>YOUR AI ASSISTANT</small></Link>
        <nav aria-label="Main navigation">
          {(["home", "chat", "projects", "knowledge", "tools", "analytics", "automations", "memory"] as DashboardView[]).map(item => <button key={item} className={view === item ? styles.active : ""} aria-current={view === item ? "page" : undefined} onClick={() => { setView(item); if (item === "memory") void memory.refresh().catch(failure => setError(failure.message)); }}><DashboardIcon name={item} /><span>{item === "automations" ? "Agent tasks" : item[0].toUpperCase() + item.slice(1)}</span></button>)}
          <Link href="/monitor" className={styles.monitorLink}><DashboardIcon name="monitor" /><span>System monitor</span></Link>
        </nav>
        <div className={styles.sidebarBottom}><div className={styles.sessionBadge}><span />Local session<small>Demo workspace</small></div><button className={view === "settings" ? styles.active : ""} onClick={() => setView("settings")}><DashboardIcon name="settings" />Settings</button></div>
      </aside>
      <main className={styles.main}>
        <header className={styles.topbar}>
          <form className={styles.search} onSubmit={(event) => { event.preventDefault(); if (searchQuery.trim()) { preparePrompt(searchQuery.trim()); setSearchQuery(""); } }}><DashboardIcon name="search" /><input ref={searchInputRef} aria-label="Search or start a conversation" placeholder="Ask anything…" value={searchQuery} onChange={event => setSearchQuery(event.target.value)} /><kbd>Ctrl K</kbd></form>
          <div className={styles.headerActions}><button aria-label="View recent activity" onClick={() => setView("analytics")}><DashboardIcon name="bell" /></button><button aria-label="Appearance settings" onClick={() => setView("settings")}><DashboardIcon name="settings" /></button><Link href="/" className={styles.profile} aria-label={`${userName} — local workspace`}><span>{userName[0]?.toUpperCase()}</span><div>{userName}<small>Local workspace</small></div></Link></div>
        </header>
        <div className={styles.workspace}>
        <DesktopVoice onActive={setDesktopVoiceActive} onInterrupt={() => { cancelAssistant(); stopVoice(); setAgentStop(value => value + 1); }} onStopListening={() => { handsFree.setEnabled(false); stopVoice(); }} />
<div className={styles.handsFreeBar}><div><strong>{handsFree.enabled ? "● Hands-free microphone on" : "Hands-free voice"}</strong><small>{handsFree.status}{handsFree.enabled ? " · Say ‘Ultron stop’ to interrupt. Use headphones to prevent speaker echo." : " · Opt-in wake phrase and 30-second follow-ups. Browser speech recognition may use a cloud service."}</small>{handsFree.enabled && handsFree.transcript && <small>Heard: {handsFree.transcript}</small>}</div><button disabled={desktopVoiceActive} aria-pressed={handsFree.enabled} onClick={() => { stopVoice(); if (!handsFree.enabled) cancelAssistant(); handsFree.setEnabled(!handsFree.enabled); }}>{handsFree.enabled ? "Turn hands-free off" : "Enable hands-free"}</button></div>
        {memory.error && <p role="alert">Local memory unavailable: {memory.error}</p>}
        <AgentWorkspace visible={view === "automations"} initialGoal={agentGoal} onState={setAgentBusy} stopSignal={agentStop} />
        {view === "memory" && <MemoryWorkspace state={memory.state} refresh={memory.refresh} fileLabel={fileLabel} fileContext={fileContext} onSelect={id => { cancelAssistant(); stopVoice(); memory.select(id); setView("chat"); }} onClear={() => { cancelAssistant(); memory.newChat(); setMessages(INITIAL_MESSAGES); }} />}
        {approval && <section className={styles.approvalPanel} role="alertdialog" aria-labelledby="control-permission-title" aria-describedby="control-permission-detail"><div><strong id="control-permission-title">Permission required · one action only</strong><p id="control-permission-detail">{approval.label}</p><small>Expires after 60 seconds. Review the destination and exact action. Say “confirm” or “cancel”, or use the buttons.</small></div><div className={styles.inlineActions}><button disabled={controlBusy || isStreaming} onClick={() => void sendToAssistant("confirm")}>Approve once</button><button onClick={() => { pendingCommandRef.current = null; setApproval(undefined); setSystemResult("Cancelled. Pending action and remaining task steps were discarded."); }}>Cancel action</button></div></section>}
        <section className={styles.hero} aria-label="Interactive assistant orb">
          <div ref={containerRef} className={`orb-root assistant-${assistantMode}`} />
          <div className={styles.heroShade} />
          <div className={styles.heroTop}><span className={styles.badge}>● {assistant.label}</span><button onClick={toggleGestures} aria-pressed={cameraOn} disabled={camera === "starting"}><DashboardIcon name="hand" />{camera === "starting" ? "Starting…" : cameraOn ? "Gestures on" : "Gesture control"}</button></div>
          <div className={styles.greeting}><h1>Welcome back, {userName}</h1><p>What would you like to do today?</p></div>
        </section>
        <section className={styles.quickActions} aria-label="Assistant shortcuts">
          {[{ title: "Chat", icon: "chat", copy: "Get instant answers", prompt: "" }, { title: "Create", icon: "create", copy: "Bring ideas to life", prompt: "Help me create " }, { title: "Analyze", icon: "analytics", copy: "Find clarity in data", prompt: "Help me analyze " }, { title: "Code", icon: "code", copy: "Build and debug", prompt: "Help me write or debug code. " }, { title: "Learn", icon: "knowledge", copy: "Explore new concepts", prompt: "Explain a new concept to me: " }].map(item => <button key={item.title} onClick={() => preparePrompt(item.prompt)}><span className={styles.featureIcon} data-tone={item.title}><DashboardIcon name={item.icon} /></span><strong>{item.title}</strong><small>{item.copy}</small></button>)}
        </section>
        {view === "chat" && <div className={styles.sectionHeading}><div><span className={styles.eyebrow}>YOUR AI COMPANION · SAVED LOCALLY</span><h1>Conversation</h1></div><button onClick={() => { cancelAssistant(); stopVoice(); memory.newChat(); setMessages(INITIAL_MESSAGES); setCommandText(""); }}><DashboardIcon name="chat" />New chat</button></div>}
        <DashboardSections view={view} onPrompt={preparePrompt} onAttach={() => fileInputRef.current?.click()} onTools={() => setView("tools")} onTestAudio={() => { if (speechOutput) speakText("Audio channel online. ULTRON voice output is active."); else setError("Enable voice output, then test audio."); }} audioEnabled={speechOutput} onToggleAudio={() => { stopSpeechOutput(); setSpeechOutput(enabled => !enabled); }} fileLabel={fileLabel} fileContext={fileContext} onClearFile={() => { setFileLabel(""); setFileContext(""); }} messageCount={messages.filter(message => message.role === "assistant" && message.id !== 1 && message.content.trim()).length} userMessages={messages.filter(message => message.role === "user").length} activity={activityLog} accent={accent} onAccent={setAccent} />
        {view === "tools" && <section className={styles.contentPanel}><div className={styles.sectionHeading}><div><span className={styles.eyebrow}>EXTEND YOUR REACH</span><h1>Tools</h1></div><span className={styles.badge}>LOCAL CONTROLS</span></div><p className={styles.secondary}>Real actions, with results from your computer. Phone controls require ADB.</p><div className={styles.toolCards}>{[{ title: "Code Assistant", icon: "code", copy: "Write, debug and understand code", action: () => preparePrompt("Help me with this code: ") }, { title: "Document Analyzer", icon: "file", copy: "Attach a text or code file", action: () => fileInputRef.current?.click() }, { title: "Desktop Control", icon: "monitor", copy: "Launch allowlisted Windows apps", action: () => document.getElementById("desktop-controls")?.scrollIntoView({ behavior: "smooth", block: "center" }) }, { title: "File Search", icon: "search", copy: "Find files in permitted folders", action: () => document.getElementById("file-controls")?.scrollIntoView({ behavior: "smooth", block: "center" }) }, { title: "Browser Launcher", icon: "globe", copy: "Open websites in your browser", action: () => document.getElementById("browser-controls")?.scrollIntoView({ behavior: "smooth", block: "center" }) }, { title: "Android Control", icon: "phone", copy: "Connect your phone using ADB", action: () => document.getElementById("phone-controls")?.scrollIntoView({ behavior: "smooth", block: "center" }) }].map(item => <button key={item.title} onClick={item.action}><span className={styles.featureIcon}><DashboardIcon name={item.icon} /></span><strong>{item.title}</strong><small>{item.copy}</small></button>)}</div></section>}

      <div className="hud assistant-panel">
        <div className="assistant-status">
          <span className={`status-dot ${assistantMode}`} />
          <div>
            <div className="status-label">{assistant.label}</div>
            <div className="status-subtitle">{assistant.subtitle}</div>
          </div>
        </div>

        <div className="waveform" aria-hidden="true">
          {audioLevels.map((level, index) => (
            <span
              key={index}
              style={{
                transform: `scaleY(${
                  assistantMode === "thinking" ? 0.35 + (index % 4) * 0.18 : level
                })`,
              }}
            />
          ))}
        </div>

        <div className="tool-strip">
          <button
            type="button"
            className="hud-btn compact"
            aria-pressed={voiceActive}
            disabled={!speechSupported || handsFree.enabled}
            onClick={toggleVoice}
          >
            {voiceActive ? "MIC ON" : "VOICE"}
          </button>
          <button
            type="button"
            className="hud-btn compact"
            disabled
            title="Provider web search is not connected yet. Use Browser Launcher in Tools to open websites."
          >
            WEB SEARCH
          </button>
          <button
            type="button"
            className="hud-btn compact"
            aria-pressed={speechOutput}
            onClick={() => setSpeechOutput((enabled) => !enabled)}
          >
            AUDIO
          </button>
          <button
            type="button"
            className="hud-btn compact"
            onClick={() => {
              if (speechOutput) speakText("Audio channel online. ULTRON voice output is active.");
              else setError("Enable AUDIO, then test voice output.");
              addLog("AUDIO", "Voice output test triggered");
            }}
          >
            TEST
          </button>
          <button type="button" className="hud-btn compact" onClick={() => fileInputRef.current?.click()}>
            FILE
          </button>
          {isStreaming && (
            <button type="button" className="hud-btn compact danger" onClick={cancelAssistant}>
              STOP
            </button>
          )}
        </div>

        <form className="command-form" onSubmit={handleSubmit}>
          <textarea
            ref={commandInputRef}
            aria-label="Message ULTRON"
            value={commandText}
            onChange={(event) => setCommandText(event.target.value)}
            placeholder={interimTranscript || "Ask ULTRON anything..."}
            rows={1}
          />
          <button type="submit" className="hud-btn compact" disabled={!commandText.trim() || isStreaming}>
            <DashboardIcon name="send" /><span className={styles.sendLabel}>Send</span>
          </button>
        </form>

        <input
          ref={fileInputRef}
          type="file"
          accept=".pdf,.docx,.txt,.md,.json,.js,.ts,.tsx,.css,.html,.py,.java,.c,.cpp,.cs,.go,.rs,.yml,.yaml,.csv,.log"
          hidden
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) void loadFileContext(file);
            event.target.value = "";
          }}
        />

        {(fileLabel || interimTranscript) && (
          <div className="context-line">
            {fileLabel && <span>FILE: {fileLabel}</span>}
            {interimTranscript && <span>HEARD: {interimTranscript}</span>}
          </div>
        )}

        <div className="chat-panel" ref={chatPanelRef} aria-live="polite" aria-relevant="additions text">
          {messages.map((message) => (
            <div className={`chat-message ${message.role}`} key={message.id}>
              <span>{message.role === "user" ? "YOU" : "ULTRON"}</span>
              <p>{message.content || "..."}</p>
            </div>
          ))}
        </div>
      </div>
      <div className={styles.suggestions} aria-label="Suggested prompts">{["Explain quantum computing", "Help me build a web app", "Analyze an attached file", "Create a study plan", "Open YouTube"].map(prompt => <button key={prompt} onClick={() => preparePrompt(prompt)}>{prompt}</button>)}</div>

      <div className="hud activity-panel">
        <div className="panel-title">ACTIVITY</div>
        {activityLog.map((item) => (
          <div className="activity-row" key={item.id}>
            <span>{item.label}</span>
            <p>{item.detail}</p>
          </div>
        ))}
      </div>

      <div className="hud system-panel">
        <div className="panel-title">SYSTEM CONTROL</div>

        <div className="system-section" id="desktop-controls">
          <span>DESKTOP</span>
          <div className="system-row">
            <select aria-label="Desktop application" value={desktopApp} onChange={(event) => setDesktopApp(event.target.value)}>
              <option value="notepad">Notepad</option>
              <option value="calculator">Calculator</option>
              <option value="paint">Paint</option>
              <option value="explorer">Explorer</option>
            </select>
            <button
              type="button"
              className="hud-btn compact"
              onClick={() => void callSystem({ action: "openApp", app: desktopApp }, `Open ${desktopApp}`)}
            >
              OPEN
            </button>
          </div>
          <div className="system-row"><button type="button" className="hud-btn compact" disabled={controlBusy || desktopApp === "explorer"} onClick={() => void callSystem({ action: "closeApp", app: desktopApp }, `Close ${desktopApp}`)}>CLOSE APP</button>{["desktop", "documents", "downloads", "project"].map(folder => <button key={folder} type="button" className="hud-btn compact" disabled={controlBusy} onClick={() => void callSystem({ action: "openFolder", root: folder }, `Open ${folder}`)}>{folder.toUpperCase()}</button>)}</div>
        </div>

        <div className="system-section" id="browser-controls">
          <span>BROWSER</span>
          <div className="system-row">
            <input aria-label="Website URL" value={browserUrl} onChange={(event) => setBrowserUrl(event.target.value)} />
            <button type="button" className="hud-btn compact" onClick={openBrowserUrl}>
              OPEN
            </button>
          </div>
          <BrowserControls snapshot={browserSnapshot} busy={controlBusy} url={browserUrl} onAction={(payload, label) => void callSystem(payload, label)} />
        </div>

        <div className="system-section" id="file-controls">
          <span>FILES</span>
          <div className="system-row">
            <select aria-label="Search folder" value={fileRoot} onChange={(event) => setFileRoot(event.target.value)}>
              <option value="documents">Documents</option>
              <option value="downloads">Downloads</option>
              <option value="desktop">Desktop</option>
              <option value="project">Project</option>
            </select>
            <input
              value={fileSearch}
              aria-label="Search filename"
              onChange={(event) => setFileSearch(event.target.value)}
              placeholder="filename"
            />
            <button
              type="button"
              className="hud-btn compact"
              onClick={() =>
                void callSystem({ action: "searchFiles", root: fileRoot, query: fileSearch }, "Search files")
              }
            >
              FIND
            </button>
          </div>
          {fileHits.length > 0 && (
            <div className="file-hit-list">
              {fileHits.map((hit) => <div key={hit.path}><span>{hit.name}</span><div className="system-row"><button type="button" disabled={controlBusy} onClick={() => void callSystem({ action: "summarizeFile", filePath: hit.path }, "Read and summarize document")}>Read / summarize</button><button type="button" disabled={controlBusy} onClick={() => void callSystem({ action: "openFile", filePath: hit.path }, "Open document")}>Open file</button></div></div>)}
            </div>
          )}
        </div>

        <div className="system-section" id="phone-controls">
          <span>ANDROID ADB</span>
          <p className={styles.secondary}>Connect an unlocked phone by USB, enable USB debugging and accept its RSA prompt. Select a device when multiple phones are attached. Every phone-changing action requires approval.</p>
          <select aria-label="Selected Android device" value={selectedDevice} onChange={event => { selectedDeviceRef.current = event.target.value; setSelectedDevice(event.target.value); }}><option value="">Auto-select single authorized phone</option>{phoneDevices.map(device => <option key={device.id} value={device.id} disabled={device.state !== "device"}>{device.id} · {device.state}</option>)}</select>
          <div className="system-row">
            <button type="button" className="hud-btn compact" onClick={() => void callSystem({ action: "adbDevices" }, "ADB devices")}>
              DEVICES
            </button>
            <button type="button" className="hud-btn compact" onClick={() => void callSystem({ action: "adbKey", key: "HOME" }, "ADB home")}>
              HOME
            </button>
            <button type="button" className="hud-btn compact" onClick={() => void callSystem({ action: "adbKey", key: "BACK" }, "ADB back")}>
              BACK
            </button>
            <button type="button" className="hud-btn compact" onClick={() => void callSystem({ action: "adbScreenshot" }, "ADB screenshot")}>
              SHOT
            </button>
          </div>
          <div className="system-row">
            <input aria-label="Android package name" value={adbPackage} onChange={(event) => setAdbPackage(event.target.value)} />
            <button
              type="button"
              className="hud-btn compact"
              onClick={() => void callSystem({ action: "adbOpenApp", packageName: adbPackage }, "ADB open app")}
            >
              APP
            </button>
          </div>
          <div className="system-row">
            <input
              value={adbTextInput}
              aria-label="Text to type on phone"
              onChange={(event) => setAdbTextInput(event.target.value)}
              placeholder="text to type"
            />
            <button
              type="button"
              className="hud-btn compact"
              onClick={() => void callSystem({ action: "adbType", text: adbTextInput }, "ADB type")}
            >
              TYPE
            </button>
          </div>
          <div className="system-row">
            <input value={adbTapX} onChange={(event) => setAdbTapX(event.target.value)} placeholder="x" />
            <input value={adbTapY} onChange={(event) => setAdbTapY(event.target.value)} placeholder="y" />
            <button
              type="button"
              className="hud-btn compact"
              onClick={() =>
                void callSystem(
                  { action: "adbTap", x: Number(adbTapX), y: Number(adbTapY) },
                  "ADB tap",
                )
              }
            >
              TAP
            </button>
          </div>
          <div className="system-row four-fields">
            <input
              value={adbSwipe.x1}
              onChange={(event) => setAdbSwipe((current) => ({ ...current, x1: event.target.value }))}
              placeholder="x1"
            />
            <input
              value={adbSwipe.y1}
              onChange={(event) => setAdbSwipe((current) => ({ ...current, y1: event.target.value }))}
              placeholder="y1"
            />
            <input
              value={adbSwipe.x2}
              onChange={(event) => setAdbSwipe((current) => ({ ...current, x2: event.target.value }))}
              placeholder="x2"
            />
            <input
              value={adbSwipe.y2}
              onChange={(event) => setAdbSwipe((current) => ({ ...current, y2: event.target.value }))}
              placeholder="y2"
            />
            <button
              type="button"
              className="hud-btn compact"
              onClick={() =>
                void callSystem(
                  {
                    action: "adbSwipe",
                    x1: Number(adbSwipe.x1),
                    y1: Number(adbSwipe.y1),
                    x2: Number(adbSwipe.x2),
                    y2: Number(adbSwipe.y2),
                    durationMs: 300,
                  },
                  "ADB swipe",
                )
              }
            >
              SWIPE
            </button>
          </div>
        </div>

        <div className="system-row">{["up", "down", "left", "right"].map(direction => <button key={direction} type="button" className="hud-btn compact" disabled={controlBusy} onClick={() => void callSystem({ action: "adbSwipe", direction }, `Swipe phone ${direction}`)}>SWIPE {direction.toUpperCase()}</button>)}</div>
        {fileLabel && <button className="hud-btn compact" onClick={() => preparePrompt("Summarize the attached document. Distinguish facts from assumptions and highlight key takeaways.")}>ASK AI ABOUT DOCUMENT</button>}
        {systemResult && <div className="system-result" role="status">{systemResult}</div>}
        {adbScreenshot && <><p className={styles.secondary}>Click the phone image to choose tap coordinates, then use TAP and approve the action.</p><img className="adb-shot" src={adbScreenshot} alt="Android screen; click to select tap coordinates" onClick={event => { const bounds = event.currentTarget.getBoundingClientRect(); setAdbTapX(String(Math.floor((event.clientX - bounds.left) / bounds.width * event.currentTarget.naturalWidth))); setAdbTapY(String(Math.floor((event.clientY - bounds.top) / bounds.height * event.currentTarget.naturalHeight))); }} /></>}
        <div className="system-section"><span>CONTROLLER AUDIT · CURRENT SERVER SESSION</span><button type="button" className="hud-btn compact" disabled={controlBusy} onClick={() => void callSystem({ action: "controlStatus" }, "Refresh control audit")}>REFRESH AUDIT</button>{controlAudit.slice(0, 15).map(event => <div className={styles.auditRow} key={event.id}><strong>{event.ok ? "OK" : "FAILED"} · {event.action}</strong><time>{new Date(event.time).toLocaleTimeString()}</time><p>{event.detail}</p></div>)}</div>
      </div>

      <div className="hud hud-hint">
        <div>
          <span className="key">DRAG</span> spin&nbsp;&nbsp;
          <span className="key">SCROLL</span> zoom
        </div>
        {cameraOn ? (
          <div>
            <span className="key">PINCH + MOVE</span> spin&nbsp;&nbsp;
            <span className="key">PINCH BOTH HANDS +/- SPREAD</span> zoom
          </div>
        ) : (
          <div>
            <span className="key">G</span> hand gestures&nbsp;&nbsp;
            <span className="key">V</span> voice&nbsp;&nbsp;
            <span className="key">ESC</span> stop&nbsp;&nbsp;
            <span className="key">R</span> reset&nbsp;&nbsp;
            <span className="key">+/-</span> zoom
          </div>
        )}
      </div>

      <div className="hud hud-controls">
        <div className={`camera-panel${cameraOn ? " visible" : ""}`}>
          <video ref={videoRef} muted playsInline className="camera-video" />
          <canvas ref={overlayRef} width={208} height={156} className="camera-overlay" />
          <div className="camera-status">
            {status.hands > 0
              ? `${status.hands} HAND${status.hands > 1 ? "S" : ""} - ${MODE_LABEL[status.mode]}`
              : "SHOW HANDS"}
          </div>
        </div>

        {error && <div className="hud-error">{error}</div>}

        <div className="hud-row">
          <button
            type="button"
            className="hud-btn"
            aria-pressed={cameraOn}
            onClick={toggleGestures}
            disabled={camera === "starting"}
          >
            {camera === "starting" ? "INITIALIZING..." : cameraOn ? "GESTURES ON" : "GESTURES OFF"}
          </button>
        </div>
        <div className="hud-row">
          <button type="button" className="hud-btn" onClick={() => sceneRef.current?.zoomIn()} aria-label="Zoom in">
            +
          </button>
          <button type="button" className="hud-btn" onClick={() => sceneRef.current?.zoomOut()} aria-label="Zoom out">
            -
          </button>
          <button type="button" className="hud-btn" onClick={resetInterface}>
            RESET
          </button>
        </div>
      </div>
        </div>
      </main>
    </div>
  );
}
