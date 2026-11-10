// Simulated pages check tab ownership and safety without opening or closing a real browser.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { EventEmitter } = require('node:events');
const ts = require('typescript');
const root = path.resolve(__dirname, '..');
function load(filename, dependencies) {
  const exports = {};
  const source = ts.transpileModule(fs.readFileSync(path.join(root, filename), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true } }).outputText;
  vm.runInNewContext(source, { exports, require: name => Object.hasOwn(dependencies, name) ? dependencies[name] : require(name), URL, Buffer, console });
  return exports;
}
const catalog = load('lib/appCatalog.ts', {});
const parser = load('lib/voiceCommands.ts', { './appCatalog': catalog });
class FakePage extends EventEmitter {
  constructor() { super(); this.address = 'about:blank'; this.closed = false; this.front = false; }
  isClosed() { return this.closed; }
  async close() { this.closed = true; }
  url() { return this.address; }
  async title() { return this.address === 'about:blank' ? '' : 'Fixture website'; }
  async goto(destination) { this.address = destination; }
  async bringToFront() { this.front = true; }
  setDefaultTimeout() {}
  setDefaultNavigationTimeout() {}
  locator() { return { elementHandles: async () => [], innerText: async () => '', }; }
  async screenshot() { return Buffer.from('fixture-image'); }
}
const pages = [];
const context = {
  pages: () => pages.filter(page => !page.closed),
  route: async () => {},
  newPage: async () => { const page = new FakePage(); pages.push(page); return page; },
};
const browser = new EventEmitter();
browser.connected = true;
browser.isConnected = () => browser.connected;
browser.newContext = async () => context;
browser.close = async () => { for (const page of pages) await page.close(); browser.connected = false; browser.emit('disconnected'); };
const controller = load('lib/browserControl.ts', {
  'playwright-core': { chromium: { launch: async () => browser } },
  'node:dns/promises': { lookup: async () => [{ address: '93.184.216.34' }] },
  './controlSecurity': { approvalFor: () => { throw new Error('Stale snapshots must fail before permission.'); } },
});
async function main() {
  for (const phrase of ['open new tab.', 'Ultron open a new tab', 'create a new tab', 'browser open new tab', 'new tab']) {
    assert.equal(parser.parseVoicePlan(phrase)[0].payload.action, 'browserNewTab');
  }
  assert.equal(parser.parseVoicePlan('open Notepad then open new tab').length, 2);
  assert.equal(parser.parseVoiceCommand('open new tab and delete everything'), null);
  assert.equal(parser.parseVoiceCommand('browser open youtube').payload.action, 'browserOpen');
  const origin = 'http://127.0.0.1:3000';
  const first = await controller.executeBrowserControl({ action: 'browserOpen', url: 'https://example.com' }, origin);
  const second = await controller.executeBrowserControl({ action: 'browserNewTab' }, origin);
  assert.equal(second.browser.url, 'about:blank');
  assert.equal(pages.length, 2);
  assert.equal(pages[0].url(), 'https://example.com/');
  assert.equal(pages[0].closed, false);
  assert.equal(pages[1].front, true);
  await assert.rejects(() => controller.executeBrowserControl({ action: 'browserScroll', direction: 'down', snapshot: first.browser.snapshot }, origin), /page changed/);
  await assert.rejects(() => controller.executeBrowserControl({ action: 'browserNewTab', url: 'file:///private' }, origin), /blank page only/);
  await assert.rejects(() => controller.executeBrowserControl({ action: 'browserOpen', url: 'http://127.0.0.1/assistant' }, origin), /Private network/);
  for (const page of pages.slice()) {
    const popup = new FakePage(); page.emit('popup', popup);
    assert.equal(popup.closed, true);
    let cancelled = false; page.emit('download', { cancel: async () => { cancelled = true; } });
    assert.equal(cancelled, true);
  }
  while (context.pages().length < 8) await controller.executeBrowserControl({ action: 'browserNewTab' }, origin);
  await assert.rejects(() => controller.executeBrowserControl({ action: 'browserNewTab' }, origin), /eight-tab limit/);
  await pages.at(-1).close();
  await controller.executeBrowserControl({ action: 'browserNewTab' }, origin);
  assert.equal(context.pages().length, 8);
  assert.equal(pages[0].closed, false);
  await controller.executeBrowserControl({ action: 'browserClose' }, origin);
  assert.equal(context.pages().length, 0);
  console.log('PASS: new-tab phrases and compound commands; existing tabs preserved; blank-tab validation; stale snapshots; private URL rejection; popup/download handling; eight-tab bound; recovery after selected tab closes. Fixtures only: no real browser launched.');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
