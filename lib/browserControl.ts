import { lookup } from "node:dns/promises";
import { isIP } from "node:net";
import { randomUUID } from "node:crypto";
import { chromium, type Browser, type BrowserContext, type ElementHandle, type Page } from "playwright-core";
import { approvalFor, type ControlPayload } from "./controlSecurity";

interface BrowserNode { id: number; label: string; tag: string; type: string; sensitive: boolean; destination: string; handle: ElementHandle; }
interface BrowserState { browser?: Browser; context?: BrowserContext; page?: Page; snapshot: string; url: string; nodes: BrowserNode[]; busy: boolean; }
const browserGlobal = globalThis as typeof globalThis & { ultronManagedBrowser?: BrowserState };
const state = browserGlobal.ultronManagedBrowser ??= { snapshot: "", url: "", nodes: [], busy: false };

function describeElement(node: Node) {
  const element = node as Element;
  const field = element as HTMLInputElement;
  const label = element.getAttribute("aria-label") || field.labels?.[0]?.textContent || field.placeholder || element.textContent || field.name || element.tagName;
  const type = field.type || "";
  const descriptor = `${label} ${field.name} ${field.autocomplete} ${type}`;
  const destination = element.tagName === "A" ? (element as HTMLAnchorElement).href : element.closest("form")?.action || window.location.href;
  return { label: label.trim().replace(/\s+/g, " ").slice(0, 100), tag: element.tagName.toLowerCase(), type, sensitive: /password|one.time|otp|credit|card.?number|cc-|cvc|cvv|social.?security/i.test(descriptor), destination };
}

function isPrivateAddress(address: string) {
  const normalized = address.toLowerCase().replace(/^\[|\]$/g, "");
  if (normalized.includes(":")) return normalized === "::" || normalized === "::1" || /^(fc|fd|fe[89ab]|ff)/.test(normalized) || normalized.startsWith("::ffff:") || normalized.startsWith("2001:db8:");
  const [first, second] = normalized.split(".").map(Number);
  return first === 0 || first === 10 || first === 127 || first >= 224 || (first === 169 && second === 254) || (first === 172 && second >= 16 && second <= 31) || (first === 192 && [0, 168].includes(second)) || (first === 100 && second >= 64 && second <= 127) || (first === 198 && [18, 19, 51].includes(second)) || (first === 203 && second === 0);
}

async function validateUrl(value: string, controllerOrigin: string, allowAssets = false) {
  // Do not let a website request reach private network devices or privileged local pages.
  const url = new URL(value);
  if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) throw new Error("Only HTTP(S) browser URLs without credentials are supported.");
  if (url.origin === controllerOrigin && (url.pathname === "/control-sandbox" || (allowAssets && url.pathname.startsWith("/_next/static/")))) return url.href;
  if (url.port && !["80", "443"].includes(url.port)) throw new Error("Only standard public web ports are supported.");
  const hostname = url.hostname.replace(/^\[|\]$/g, "");
  if (hostname === "localhost" || hostname.endsWith(".local") || !hostname.includes(".")) throw new Error("Private network and controller pages cannot be automated.");
  const addresses = isIP(hostname) ? [{ address: hostname }] : await lookup(hostname, { all: true });
  if (!addresses.length || addresses.some(item => isPrivateAddress(item.address))) throw new Error("Private network addresses cannot be automated.");
  return url.href;
}

async function currentPage() {
  if (!state.page || state.page.isClosed()) throw new Error("Open a managed browser page first.");
  return state.page;
}

async function openPage(url: string, origin: string, newTab = false) {
  // New tabs belong to ULTRON's isolated session, not to the user's normal browser window.
  const destination = newTab ? "about:blank" : await validateUrl(url, origin);
  if (state.browser && (!state.browser.isConnected() || !state.context)) {
    await state.browser.close().catch(() => {});
    state.browser = undefined;
    state.context = undefined;
    state.page = undefined;
  }
  if (!state.browser?.isConnected()) {
    state.browser = await chromium.launch({ channel: "msedge", headless: false });
    state.browser.on("disconnected", () => { state.page = undefined; state.context = undefined; state.nodes = []; state.snapshot = ""; });
    state.context = await state.browser.newContext({ viewport: { width: 1100, height: 740 }, acceptDownloads: false, serviceWorkers: "block" });
    await state.context.route("**/*", async route => {
      try { await validateUrl(route.request().url(), origin, true); await route.continue(); }
      catch { await route.abort("blockedbyclient"); }
    });
  }
  if (!state.page || state.page.isClosed()) state.page = state.context!.pages().filter(page => !page.isClosed()).at(-1);
  if (newTab || !state.page) {
    if (state.context!.pages().length >= 8) throw new Error("ULTRON's managed browser has reached its eight-tab limit. Close an unused managed tab first.");
    state.page = await state.context!.newPage();
    state.page.on("popup", popup => { void popup.close(); });
    state.page.on("download", download => { void download.cancel(); });
    state.page.on("dialog", dialog => { void dialog.dismiss(); });
    state.page.setDefaultTimeout(6000);
    state.page.setDefaultNavigationTimeout(20000);
  }
  const page = await currentPage();
  if (!newTab) await page.goto(destination, { waitUntil: "domcontentloaded" });
  await page.bringToFront();
}

async function inspectPage() {
  // Give visible page controls temporary numbers that later commands can refer to safely.
  const page = await currentPage();
  await Promise.all(state.nodes.map(node => node.handle.dispose().catch(() => {})));
  state.nodes = [];
  state.snapshot = randomUUID();
  state.url = page.url();
  const handles = await page.locator('a[href],button,input:not([type="hidden"]),textarea,select,[role="button"]').elementHandles();
  for (const handle of handles) {
    if (state.nodes.length >= 60) { await handle.dispose(); continue; }
    if (!await handle.isVisible().catch(() => false)) { await handle.dispose(); continue; }
    const metadata = await handle.evaluate(describeElement);
    state.nodes.push({ id: state.nodes.length + 1, ...metadata, handle });
  }
  const screenshot = await page.screenshot({ type: "jpeg", quality: 60, mask: [page.locator('input[type="password"],input[autocomplete^="cc-"],input[autocomplete="one-time-code"]')] });
  const text = (await page.locator("body").innerText()).slice(0, 18000);
  return { ok: true, message: `Managed browser: ${await page.title()}`, text, browser: { url: state.url, title: await page.title(), snapshot: state.snapshot, elements: state.nodes.map(({ id, label, tag, type, sensitive, destination }) => ({ id, label, tag, type, sensitive, destination })), screenshot: `data:image/jpeg;base64,${screenshot.toString("base64")}` } };
}

export async function executeBrowserControl(payload: ControlPayload, origin: string) {
  if (state.busy) throw new Error("Another browser action is running. Wait for its result.");
  state.busy = true;
  try {
    if (payload.action === "browserNewTab") {
      if (payload.url !== undefined) throw new Error("New-tab commands open a blank page only. Use browser open for a website.");
      await openPage("about:blank", origin, true);
      return { ...await inspectPage(), message: "Opened a new blank tab in ULTRON's managed Edge browser. Existing tabs were kept; your personal browser tabs were not changed." };
    }
    if (payload.action === "browserOpen") {
      await openPage(payload.url === "sandbox" ? `${origin}/control-sandbox` : String(payload.url || ""), origin);
      return await inspectPage();
    }
    if (payload.action === "browserClose") {
      if (payload.expectedHost && (!state.page || state.page.isClosed() || new URL(state.page.url()).hostname !== payload.expectedHost)) throw new Error('That website is not open in the ULTRON managed browser. Your other browser windows will not be closed.');
      await state.browser?.close();
      return { ok: true, message: "Closed ULTRON's isolated browser. Your normal browser was not changed.", browserClosed: true };
    }
    const page = await currentPage();
    if (payload.action === "browserInspect") return await inspectPage();
    // Old element numbers must not be reused after the page changes.
    if (!state.snapshot || payload.snapshot !== state.snapshot || state.url !== page.url()) throw new Error("The page changed. Inspect the browser again before acting.");
    const node = state.nodes.find(item => item.id === Number(payload.nodeId));
    let label: string;
    if (["browserClick", "browserFill"].includes(payload.action)) {
      if (!node) throw new Error("Select a current browser element first.");
      if (!await node.handle.isVisible() || !await node.handle.isEnabled()) throw new Error("The target is no longer available. Inspect again.");
      const current = await node.handle.evaluate(describeElement);
      if (current.label !== node.label || current.type !== node.type || current.destination !== node.destination || current.sensitive !== node.sensitive) throw new Error("The target changed. Inspect the page again and request a new approval.");
      if (node.sensitive || /buy now|purchase|pay\b|checkout|delete|erase|remove account|sign[ -]?in|log[ -]?in|captcha|accept.*terms|transfer money/i.test(node.label)) throw new Error("Sensitive, financial, authentication, and destructive actions require manual takeover in the browser.");
      if (payload.action === "browserFill" && !["input", "textarea"].includes(node.tag)) throw new Error("Choose a text input or textarea to fill.");
      if (payload.action === "browserFill" && ["file", "password", "hidden", "submit", "button", "checkbox", "radio"].includes(node.type)) throw new Error("This input type cannot be filled by ULTRON.");
      label = `${payload.action === "browserFill" ? `Fill with ${JSON.stringify(String(payload.text || ""))}` : "Click"} element ${node.id}: ${node.label} on ${page.url()}. Destination: ${node.destination}`;
    } else if (["browserBack", "browserScroll"].includes(payload.action)) label = `${payload.action === "browserBack" ? "Navigate back" : `Scroll ${payload.direction === "up" ? "up" : "down"}`} on ${page.url()}`;
    else throw new Error("Unsupported browser action.");
    const approval = approvalFor(payload, label);
    if (approval) return { ok: false, approval };
    if (payload.action === "browserClick") await node!.handle.click();
    else if (payload.action === "browserFill") await node!.handle.fill(String(payload.text || ""));
    else if (payload.action === "browserBack") await page.goBack({ waitUntil: "domcontentloaded" });
    else await page.evaluate(direction => window.scrollBy(0, direction === "up" ? -550 : 550), payload.direction);
    return await inspectPage();
  } finally { state.busy = false; }
}
