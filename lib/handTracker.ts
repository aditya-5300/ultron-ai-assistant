import type {
  HandLandmarker,
  NormalizedLandmark,
} from "@mediapipe/tasks-vision";
import { GestureZoom } from "./gestureZoom";

const WASM_CDN =
  "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.35/wasm";
const MODEL_URL =
  "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task";

// Landmark indices (MediaPipe hand model)
const WRIST = 0;
const THUMB_TIP = 4;
const INDEX_TIP = 8;
const MIDDLE_MCP = 9;

// Pinch hysteresis: thumb–index distance relative to hand size
const PINCH_ON = 0.32;
const PINCH_OFF = 0.45;

// How strongly hand movement rotates the orb (radians per normalized unit)
const ROTATE_SPEED = 5.0;
const PINCH_STABLE_MS = 60;

export type GestureMode = "idle" | "spin" | "zoom";

export interface TrackerStatus {
  hands: number;
  mode: GestureMode;
}

export interface HandTrackerCallbacks {
  /** Called when a single pinched hand drags: deltas in mirrored normalized coords. */
  onRotate(deltaTheta: number, deltaPhi: number): void;
  /** Called when both hands pinch and spread/close: multiply camera distance by factor. */
  onZoom(factor: number): void;
  onStatus(status: TrackerStatus): void;
}

interface Point {
  x: number;
  y: number;
}

interface HandState {
  pinching: boolean;
  candidatePinch: boolean;
  candidateSince: number;
  grab: Point; // smoothed pinch midpoint, mirrored
}

export class HandTracker {
  private video: HTMLVideoElement;
  private overlay: HTMLCanvasElement;
  private callbacks: HandTrackerCallbacks;
  private landmarker: HandLandmarker | null = null;
  private stream: MediaStream | null = null;
  private rafId = 0;
  private running = false;
  private lastVideoTime = -1;

  // keyed by handedness label so state survives re-ordering between frames
  private handStates = new Map<string, HandState>();
  private prevMode: GestureMode = "idle";
  private prevSpinGrab: Point | null = null;
  private zoom = new GestureZoom();
  private lastFrameTime = 0;
  private activeHands = "";
  private lastStatus: TrackerStatus = { hands: 0, mode: "idle" };

  constructor(
    video: HTMLVideoElement,
    overlay: HTMLCanvasElement,
    callbacks: HandTrackerCallbacks,
  ) {
    this.video = video;
    this.overlay = overlay;
    this.callbacks = callbacks;
  }

  async start(): Promise<void> {
    // The camera feeds local hand tracking; this feature does not need microphone audio.
    this.stream = await navigator.mediaDevices.getUserMedia({
      video: { width: 640, height: 480, facingMode: "user" },
      audio: false,
    });
    this.video.srcObject = this.stream;
    await this.video.play();

    const { FilesetResolver, HandLandmarker } = await import("@mediapipe/tasks-vision");
    const fileset = await FilesetResolver.forVisionTasks(WASM_CDN);
    const options = {
      baseOptions: { modelAssetPath: MODEL_URL, delegate: "GPU" as const },
      runningMode: "VIDEO" as const,
      numHands: 2,
      minHandDetectionConfidence: 0.6,
      minHandPresenceConfidence: 0.6,
      minTrackingConfidence: 0.6,
    };
    try {
      this.landmarker = await HandLandmarker.createFromOptions(fileset, options);
    } catch {
      // Some browsers/GPUs reject the GPU delegate — fall back to CPU
      this.landmarker = await HandLandmarker.createFromOptions(fileset, {
        ...options,
        baseOptions: { ...options.baseOptions, delegate: "CPU" as const },
      });
    }

    this.running = true;
    this.loop();
  }

  stop(): void {
    this.running = false;
    cancelAnimationFrame(this.rafId);
    this.landmarker?.close();
    this.landmarker = null;
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = null;
    this.video.srcObject = null;
    this.handStates.clear();
    this.prevMode = "idle";
    this.prevSpinGrab = null;
    this.zoom.reset();
    this.lastFrameTime = 0;
    this.activeHands = "";
    this.lastVideoTime = -1;
    const ctx = this.overlay.getContext("2d");
    ctx?.clearRect(0, 0, this.overlay.width, this.overlay.height);
    this.emitStatus({ hands: 0, mode: "idle" });
  }

  private loop = () => {
    if (!this.running) return;
    this.rafId = requestAnimationFrame(this.loop);

    if (!this.landmarker || this.video.readyState < 2) return;
    if (this.video.currentTime === this.lastVideoTime) return;
    this.lastVideoTime = this.video.currentTime;

    const result = this.landmarker.detectForVideo(this.video, performance.now());
    this.processHands(result.landmarks, result.handedness.map((h) => h[0]?.categoryName ?? "?"));
    this.drawOverlay(result.landmarks, result.handedness.map((hand) => hand[0]?.categoryName ?? "?"));
  };

  private processHands(
    landmarks: NormalizedLandmark[][],
    labels: string[],
  ): void {
    const time = performance.now();
    const elapsed = this.lastFrameTime ? Math.min(100, Math.max(1, time - this.lastFrameTime)) : 33;
    if (this.lastFrameTime && time - this.lastFrameTime > 250) {
      this.handStates.clear();
      this.prevSpinGrab = null;
      this.zoom.reset();
    }
    this.lastFrameTime = time;
    const aspect = this.video.videoWidth > 0 && this.video.videoHeight > 0
      ? this.video.videoWidth / this.video.videoHeight : 4 / 3;
    const distance = (first: Point, second: Point) => Math.hypot((first.x - second.x) * aspect, first.y - second.y);
    const uniqueLabels = new Set(labels);
    if (uniqueLabels.size !== landmarks.length || labels.some(label => !label || label === "?")) {
      this.handStates.clear(); this.prevSpinGrab = null; this.zoom.reset(); this.prevMode = "idle";
      this.emitStatus({ hands: landmarks.length, mode: "idle" });
      return;
    }
    const pinchedGrabs: Point[] = [];
    const pinchedLabels: string[] = [];
    const seen = new Set<string>();
    let rebase = false;

    landmarks.forEach((lm, i) => {
      const label = labels[i];
      if (lm.length <= MIDDLE_MCP || !lm.every(point => Number.isFinite(point.x) && Number.isFinite(point.y))) return;
      const handScale = distance(lm[WRIST], lm[MIDDLE_MCP]);
      if (handScale < 0.035) return;
      seen.add(label);
      const pinchRatio = distance(lm[THUMB_TIP], lm[INDEX_TIP]) / handScale;

      // Mirrored so hand-right = screen-right from the user's perspective
      const raw: Point = {
        x: 1 - (lm[THUMB_TIP].x + lm[INDEX_TIP].x) / 2,
        y: (lm[THUMB_TIP].y + lm[INDEX_TIP].y) / 2,
      };

      let state = this.handStates.get(label);
      if (!state) {
        state = { pinching: false, candidatePinch: false, candidateSince: time, grab: raw };
        this.handStates.set(label, state);
      }

      // Hysteresis so the pinch doesn't flicker on/off at the threshold
      const requestedPinch = state.pinching ? pinchRatio <= PINCH_OFF : pinchRatio < PINCH_ON;
      if (requestedPinch !== state.candidatePinch) {
        state.candidatePinch = requestedPinch;
        state.candidateSince = time;
      }
      if (time - state.candidateSince >= PINCH_STABLE_MS) state.pinching = state.candidatePinch;

      const movement = distance(raw, state.grab);
      if (movement > 0.2) { state.grab = raw; rebase = true; }
      const smoothing = 1 - Math.exp(-elapsed / (movement > handScale * 0.15 ? 35 : 85));
      if (state.candidatePinch === state.pinching) {
        state.grab = {
          x: state.grab.x + (raw.x - state.grab.x) * smoothing,
          y: state.grab.y + (raw.y - state.grab.y) * smoothing,
        };
      }

      if (state.pinching) { pinchedGrabs.push(state.grab); pinchedLabels.push(label); }
    });

    // Drop state for hands that left the frame
    for (const key of this.handStates.keys()) {
      if (!seen.has(key)) this.handStates.delete(key);
    }

    const mode: GestureMode =
      pinchedGrabs.length >= 2 ? "zoom" : pinchedGrabs.length === 1 ? "spin" : "idle";

    // Reset reference points on any mode change to avoid jumps
    const activeHands = pinchedLabels.sort().join(":");
    if (mode !== this.prevMode || activeHands !== this.activeHands || rebase) {
      this.prevSpinGrab = null;
      this.zoom.reset();
      this.prevMode = mode;
      this.activeHands = activeHands;
    }

    if (mode === "spin") {
      const grab = pinchedGrabs[0];
      if (this.prevSpinGrab) {
        const dx = grab.x - this.prevSpinGrab.x;
        const dy = grab.y - this.prevSpinGrab.y;
        if (Math.hypot(dx * aspect, dy) > 0.002) {
          this.callbacks.onRotate(dx * ROTATE_SPEED, dy * ROTATE_SPEED);
          this.prevSpinGrab = grab;
        }
      } else this.prevSpinGrab = grab;
    } else if (mode === "zoom") {
      const factor = this.zoom.update(distance(pinchedGrabs[0], pinchedGrabs[1]), time);
      if (factor !== null) this.callbacks.onZoom(factor);
    }

    this.emitStatus({ hands: landmarks.length, mode });
  }

  private emitStatus(status: TrackerStatus): void {
    if (
      status.hands !== this.lastStatus.hands ||
      status.mode !== this.lastStatus.mode
    ) {
      this.lastStatus = status;
      this.callbacks.onStatus(status);
    }
  }

  private drawOverlay(landmarks: NormalizedLandmark[][], labels: string[]): void {
    const ctx = this.overlay.getContext("2d");
    if (!ctx) return;
    const { width, height } = this.overlay;
    ctx.clearRect(0, 0, width, height);

    for (const [handIndex, lm] of landmarks.entries()) {
      if (lm.length <= MIDDLE_MCP) continue;
      const thumb = lm[THUMB_TIP];
      const index = lm[INDEX_TIP];
      // Overlay canvas sits on the mirrored video preview, so mirror x here too
      const tx = (1 - thumb.x) * width;
      const ty = thumb.y * height;
      const ix = (1 - index.x) * width;
      const iy = index.y * height;

      const pinched = this.handStates.get(labels[handIndex])?.pinching ?? false;

      ctx.strokeStyle = pinched ? "#ffcc66" : "rgba(255,170,48,0.5)";
      ctx.lineWidth = pinched ? 2 : 1;
      ctx.beginPath();
      ctx.moveTo(tx, ty);
      ctx.lineTo(ix, iy);
      ctx.stroke();

      ctx.fillStyle = pinched ? "#ffcc66" : "rgba(255,170,48,0.7)";
      for (const [x, y] of [
        [tx, ty],
        [ix, iy],
      ]) {
        ctx.beginPath();
        ctx.arc(x, y, pinched ? 5 : 3, 0, Math.PI * 2);
        ctx.fill();
      }
    }
  }
}
