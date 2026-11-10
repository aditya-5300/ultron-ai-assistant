const paths: Record<string, string> = {
  home: "m3 10 9-7 9 7v10H5V10m4 10v-7h6v7",
  chat: "M4 4h16v12H9l-5 4V4",
  projects: "M3 7h7l2-3h9v16H3V7",
  knowledge: "M12 5C8 2 3 4 3 4v15s5-2 9 1c4-3 9-1 9-1V4s-5-2-9 1v15",
  tools: "m14 5 5 5M3 21l10-10M13 3l-2 6 4 4 6-2V4l-4 4-3-3 4-4",
  analytics: "M4 20V12h3v8m4 0V4h3v16m4 0V8h3v12",
  automations: "m13 2-9 12h7l-1 8 10-12h-7l1-8",
  settings: "m9 3 1-1h4l1 3 3 1 3-1 2 4-2 3v3l2 2-2 4-3-1-3 1-1 3h-4l-1-3-3-1-3 1-2-4 2-2v-3L1 9l2-4 3 1 3-1V3M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8",
  search: "M10 3a7 7 0 1 0 0 14 7 7 0 0 0 0-14m5 12 6 6",
  bell: "M6 9a6 6 0 0 1 12 0v6l2 3H4l2-3V9m4 12h4",
  create: "m4 20 9-9m-2-4 6-6 6 6-6 6-6-6M4 3v4M2 5h4M18 17v4m-2-2h4",
  code: "m8 6-6 6 6 6m8-12 6 6-6 6M14 3l-4 18",
  hand: "M7 12V6a2 2 0 0 1 4 0v5-7a2 2 0 0 1 4 0v7-5a2 2 0 0 1 4 0v7-3a2 2 0 0 1 4 0v6c0 5-3 7-8 7-3 0-5-2-7-5l-4-5a2 2 0 0 1 3-2l4 3",
  mic: "M9 4a3 3 0 0 1 6 0v8a3 3 0 0 1-6 0V4M5 10v2a7 7 0 0 0 14 0v-2M12 19v4m-4 0h8",
  file: "M5 2h9l5 5v15H5V2m9 0v6h5M8 12h8m-8 4h6",
  send: "m3 3 19 9-19 9 4-9-4-9m4 9h15",
  globe: "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20m0 0c-6 6-6 14 0 20 6-6 6-14 0-20M2 12h20",
  monitor: "M2 3h20v14H2V3m10 14v5m-5 0h10",
  phone: "M6 2h12v20H6V2m4 17h4",
  arrow: "M4 12h16m-6-6 6 6-6 6",
};

export default function DashboardIcon({ name }: { name: string }) {
  return <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name] || paths.chat} /></svg>;
}
