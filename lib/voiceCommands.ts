import { appIdForAlias } from './appCatalog';

export interface VoiceCommand {
  payload: Record<string, unknown>;
  label: string;
  confirm?: boolean;
}

export function normalizeCommand(text: string) {
  // Remove the assistant's name and polite opening words before looking for a command.
  return text.trim().replace(/^(?:hey\s+)?ultron[\s,.!:;-]*/i, "").replace(/^(?:(?:can|could|would|will) you\s+)?(?:please\s+)?/i, "").replace(/[.!?]+$/, "").trim();
}

export function isAgentCommand(text: string) {
  return /^(?:plan\b|research\b|(?:make|write|create)\b.*\breport\b|book\b)/i.test(normalizeCommand(text));
}

function elementNumber(text: string) {
  const normalized = text.toLowerCase().trim().replace(/-/g, " ");
  if (/^\d+$/.test(normalized)) return Number(normalized);
  const units = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"];
  if (units.includes(normalized)) return units.indexOf(normalized);
  const match = normalized.match(/^(twenty|thirty|forty|fifty|sixty)(?: (one|two|three|four|five|six|seven|eight|nine))?$/);
  if (!match) return null;
  const tens: Record<string, number> = { twenty: 20, thirty: 30, forty: 40, fifty: 50, sixty: 60 };
  return tens[match[1]] + (match[2] ? units.indexOf(match[2]) : 0);
}

function coordinateNumber(text: string): number | null {
  const normalized = text.toLowerCase().trim().replace(/-/g, " ");
  if (/^\d{1,5}$/.test(normalized)) return Number(normalized);
  const small: Record<string, number> = { zero: 0, one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9, ten: 10, eleven: 11, twelve: 12, thirteen: 13, fourteen: 14, fifteen: 15, sixteen: 16, seventeen: 17, eighteen: 18, nineteen: 19 };
  const tens: Record<string, number> = { twenty: 20, thirty: 30, forty: 40, fifty: 50, sixty: 60, seventy: 70, eighty: 80, ninety: 90 };
  const words = normalized.split(/\s+/);
  if (words.length === 1) return small[words[0]] ?? tens[words[0]] ?? null;
  if (words.length === 2 && tens[words[0]] && small[words[1]] > 0 && small[words[1]] < 10) return tens[words[0]] + small[words[1]];
  const scaled = normalized.match(/^(one|two|three|four|five|six|seven|eight|nine) (hundred|thousand)(?: (?:and )?(.+))?$/);
  if (!scaled) return null;
  const remainder = scaled[3] ? coordinateNumber(scaled[3]) : 0;
  const scale = scaled[2] === "hundred" ? 100 : 1000;
  if (remainder === null || remainder >= scale) return null;
  return small[scaled[1]] * scale + remainder;
}

function tapCoordinates(text: string): [number, number] | null {
  // Reject unclear number pairs rather than risk tapping the wrong place on a phone.
  const normalized = text.trim().replace(/\bcomma\b/gi, ",");
  const explicit = normalized.match(/^(?:x\s+)?(.+?)(?:\s*,\s*|\s+by\s+|\s+y\s+)(.+)$/i);
  if (explicit) {
    const horizontal = coordinateNumber(explicit[1]);
    const vertical = coordinateNumber(explicit[2]);
    return horizontal !== null && vertical !== null ? [horizontal, vertical] : null;
  }
  if (coordinateNumber(normalized) !== null) return null;
  const words = normalized.split(/\s+/);
  if (words.length > 20) return null;
  const candidates: Array<[number, number]> = [];
  for (let index = 1; index < words.length; index++) {
    const horizontal = coordinateNumber(words.slice(0, index).join(" "));
    const vertical = coordinateNumber(words.slice(index).join(" "));
    if (horizontal !== null && vertical !== null) candidates.push([horizontal, vertical]);
  }
  return candidates.length === 1 ? candidates[0] : null;
}

export function parseVoiceCommand(text: string): VoiceCommand | null {
  // Convert known phrases into named actions, never into commands for a system terminal.
  const command = normalizeCommand(text);
  const power = command.match(/^(shut\s*down|restart|reboot)\s+(?:(?:the|my|this)\s+)?(?:pc|computer)$/i);
  if (power) {
    const kind = /^shut/i.test(power[1]) ? "shutdown" : "restart";
    return { payload: { action: "schedulePower", kind }, label: `Schedule PC ${kind}`, confirm: true };
  }
  const cancelPower = command.match(/^cancel\s+(?:the\s+)?(shut\s*down|restart)$/i);
  if (cancelPower) return { payload: { action: "cancelPower", kind: /^shut/i.test(cancelPower[1]) ? "shutdown" : "restart" }, label: "Cancel the pending power action" };
  if (/^(?:show commands|help with commands)$/i.test(command)) return { payload: { action: "commandHelp" }, label: "Show supported command examples" };
  if (/^(?:browser\s+)?(?:(?:open|create)\s+(?:(?:a|the)\s+)?)?new tab$/i.test(command)) return { payload: { action: "browserNewTab" }, label: "Open a new blank tab in ULTRON's managed Edge browser" };
  const onPhone = command.match(/^(?:open|launch) ([a-z0-9_.]+) on (?:my )?(?:phone|android)$/i);
  if (onPhone) return parseVoiceCommand(`phone open ${onPhone[1]}`);
  const browserOpen = command.match(/^browser (?:open|go to) (.+)$/i);
  if (browserOpen) {
    const destinations: Record<string, string> = { youtube: "https://www.youtube.com", google: "https://www.google.com", github: "https://github.com", sandbox: "sandbox", demo: "sandbox" };
    const target = browserOpen[1].trim().replace(/\s+dot\s+/gi, ".").replace(/\s*([./:])\s*/g, "$1");
    return { payload: { action: "browserOpen", url: destinations[target.toLowerCase()] || (/^https?:/i.test(target) ? target : `https://${target}`) }, label: `Open managed browser: ${target}` };
  }
  const browserAction = command.match(/^browser (inspect|refresh|back|close|scroll up|scroll down)$/i);
  if (browserAction) {
    const action = browserAction[1].toLowerCase();
    return { payload: { action: action.startsWith("scroll") ? "browserScroll" : action === "back" ? "browserBack" : action === "close" ? "browserClose" : "browserInspect", ...(action.startsWith("scroll") ? { direction: action.split(" ")[1] } : {}) }, label: `Browser ${action}` };
  }
  const browserClick = command.match(/^browser click (?:element )?(.+)$/i);
  if (browserClick) {
    const nodeId = elementNumber(browserClick[1]);
    if (nodeId !== null && nodeId >= 1 && nodeId <= 60) return { payload: { action: "browserClick", nodeId }, label: `Click browser element ${nodeId}`, confirm: true };
    return null;
  }
  const browserFill = command.match(/^browser fill (?:element )?(.+?) with (.+)$/i);
  if (browserFill) {
    const nodeId = elementNumber(browserFill[1]);
    if (nodeId !== null && nodeId >= 1 && nodeId <= 60) return { payload: { action: "browserFill", nodeId, text: browserFill[2] }, label: `Fill browser element ${nodeId}`, confirm: true };
    return null;
  }
  const swipeDirection = command.match(/^(?:android|phone) swipe (up|down|left|right)$/i);
  if (swipeDirection) return { payload: { action: "adbSwipe", direction: swipeDirection[1].toLowerCase() }, label: `Phone swipe ${swipeDirection[1]}`, confirm: true };
  const phoneDevice = command.match(/^(?:phone|android) use device ([a-zA-Z0-9_.:-]+)$/i);
  if (phoneDevice) return { payload: { action: "adbSelectDevice", deviceId: phoneDevice[1] }, label: `Select phone ${phoneDevice[1]}` };
  if (/^(?:scan (?:the |my )?(?:pc|computer)(?: and check for (?:any )?malicious files)?|run (?:a )?(?:security|malware|defender)(?: quick)? scan)$/i.test(command)) return { payload: { action: 'securityScan' }, label: 'Start Microsoft Defender quick scan', confirm: true };
  if (/^(?:end|stop) (?:the )?whatsapp(?: task)?$/i.test(command)) return { payload: { action: 'closeApp', app: 'whatsapp' }, label: 'Request WhatsApp to close', confirm: true };
  const websites: Record<string, string> = { youtube: "https://www.youtube.com", "you tube": "https://www.youtube.com", google: "https://www.google.com", github: "https://github.com" };
  const launch = command.match(/^(open|launch|start|close|quit|exit|go to)\b[\s,.]*(.+)$/i);
  if (launch) {
    const target = launch[2].toLowerCase().trim().replace(/^the\s+/, "");
    const closing = /^(close|quit|exit)$/i.test(launch[1]);
    const appId = appIdForAlias(target);
    if (appId) return { payload: { action: closing ? "closeApp" : "openApp", app: appId }, label: `${closing ? "Close" : "Open"} ${target}`, confirm: closing };
    if (!closing) {
      const drive = target.match(/^(?:drive\s+([a-z])|([a-z])(?::[\\/]?)?(?:\s+drive)?)$/i);
      if (drive) return { payload: { action: 'openDrive', drive: (drive[1] || drive[2]).toUpperCase() }, label: `Open ${(drive[1] || drive[2]).toUpperCase()}: drive in File Explorer` };
      if (/^(desktop|documents|downloads|project)( folder)?$/.test(target)) return { payload: { action: "openFolder", root: target.split(" ")[0] }, label: `Open ${target}` };
      const rawTarget = launch[2].trim();
      const url = websites[target] || (/^(https?:\/\/)?[a-z0-9-]+(\.[a-z0-9-]+)+(\/[^\s]*)?$/i.test(rawTarget) ? (/^https?:/i.test(rawTarget) ? rawTarget : `https://${rawTarget}`) : null);
      if (url) return { payload: { action: "openWebsite", url }, label: `Open ${url}` };
    }
  }
  const openFile = command.match(/^open file (.+)$/i);
  if (/^open file (?:manager|explorer)\b/i.test(command)) return null;
  if (openFile) return { payload: { action: "openFile", filePath: openFile[1].replace(/^"|"$/g, "") }, label: "Open local document" };
  const search = command.match(/^(?:find|search for|search) files? (?:for |named )?(.+?)(?: in (desktop|documents|downloads|project))?$/i);
  if (search) return { payload: { action: "searchFiles", query: search[1], root: search[2]?.toLowerCase() || "documents" }, label: `Find files: ${search[1]}` };
  const summary = command.match(/^summari[sz]e (?:file )?(.+)$/i);
  if (summary) return { payload: { action: "summarizeFile", filePath: summary[1].replace(/^"|"$/g, "") }, label: "Summarize local text file" };
  if (/^(?:list |show )?(?:android|phone|adb) devices$/i.test(command)) return { payload: { action: "adbDevices" }, label: "List Android devices" };
  if (/^(?:android|phone) (?:screenshot|screen shot)$/i.test(command)) return { payload: { action: "adbScreenshot" }, label: "Capture phone screenshot" };
  const key = command.match(/^(?:android|phone) (home|back|enter|power)$/i);
  if (key) return { payload: { action: "adbKey", key: key[1].toUpperCase() }, label: `Phone ${key[1]}`, confirm: true };
  const phoneApp = command.match(/^(?:android|phone) open ([a-z0-9_.]+)$/i);
  if (phoneApp) {
    const packages: Record<string, string> = { settings: "com.android.settings", youtube: "com.google.android.youtube", chrome: "com.android.chrome", camera: "com.android.camera" };
    return { payload: { action: "adbOpenApp", packageName: packages[phoneApp[1].toLowerCase()] || phoneApp[1] }, label: `Open phone app ${phoneApp[1]}`, confirm: true };
  }
  const tap = command.match(/^(?:android|phone) tap (.+)$/i);
  if (tap) {
    const point = tapCoordinates(tap[1]);
    if (!point) return null;
    return { payload: { action: "adbTap", x: point[0], y: point[1] }, label: `Phone tap ${point[0]}, ${point[1]}`, confirm: true };
  }
  const swipe = command.match(/^(?:android|phone) swipe (\d+)[,\s]+(\d+)[,\s]+(\d+)[,\s]+(\d+)$/i);
  if (swipe) return { payload: { action: "adbSwipe", x1: Number(swipe[1]), y1: Number(swipe[2]), x2: Number(swipe[3]), y2: Number(swipe[4]) }, label: "Swipe phone screen", confirm: true };
  const typing = command.match(/^(?:android|phone) type (.+)$/i);
  if (typing) return { payload: { action: "adbType", text: typing[1] }, label: `Type on phone: ${typing[1]}`, confirm: true };
  return null;
}

export function isSystemCommand(text: string) {
  return /^(open|launch|start|close|quit|exit|go to|find|search|summarize|summarise|phone|android|adb|browser|task|click|type|delete|run|execute|show commands|help with commands|shut\s*down|restart|reboot|cancel\s+(?:the\s+)?(?:shut\s*down|restart))\b/i.test(normalizeCommand(text));
}

export function parseVoicePlan(text: string): VoiceCommand[] | null {
  const normalized = normalizeCommand(text).replace(/^task:\s*/i, "");
  const explorerDrive = normalized.match(/^(?:open|launch|start|go to)\s+(?:the\s+)?(?:file manager|file explorer|explorer|windows explorer|windows file manager)\s+(?:and(?: then)?|then|to|in|at)\s+(?:(?:open|go to)\s+)?(?:the\s+)?((?:drive\s+[a-z]|[a-z](?::[\\/]?)?(?:\s+drive)?))$/i);
  if (explorerDrive) {
    const drive = parseVoiceCommand(`open ${explorerDrive[1]}`);
    return drive ? [drive] : null;
  }
  if (/^(browser fill .* with |(?:phone|android) type )/i.test(normalized)) {
    // Everything after a typing instruction is text to enter, not another action to execute.
    const single = parseVoiceCommand(normalized);
    return single ? [single] : null;
  }
  const parts = normalized.split(/\s+(?:and then|then)\s+|;\s*|\s+and\s+(?=(?:(?:hey\s+)?ultron[\s,.!:;-]*)?(?:open|launch|close|end|stop|browser|phone|android|find|search|summarize)\b)/i);
  if (parts.length > 6) return null;
  const commands = parts.map(parseVoiceCommand);
  if (commands.length > 1 && commands.some(command => command?.payload.action === "schedulePower" || command?.payload.action === "cancelPower")) return null;
  return commands.every((command): command is VoiceCommand => command !== null) ? commands : null;
}
