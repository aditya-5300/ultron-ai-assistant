export interface BrowserSnapshot {
  url: string;
  title: string;
  snapshot: string;
  screenshot: string;
  elements: Array<{ id: number; label: string; tag: string; type: string; sensitive: boolean; destination?: string }>;
}

export interface ControlReply {
  ok: boolean;
  error?: string;
  message?: string;
  output?: string;
  summary?: string;
  text?: string;
  truncated?: boolean;
  filePath?: string;
  dataUrl?: string;
  deviceId?: string;
  devices?: Array<{ id: string; state: string; details: string }>;
  results?: Array<{ name: string; path: string; size: number }>;
  approval?: { token: string; label: string; expires: number };
  browser?: BrowserSnapshot;
  browserClosed?: boolean;
  events?: Array<{ id: string; time: string; action: string; ok: boolean; detail: string }>;
}
