const { setTimeout: delay } = require('node:timers/promises');

async function run() {
  // Ask the local server for ready work; this worker cannot approve actions on the user's behalf.
  const origin = process.env.ULTRON_WORKER_ORIGIN;
  const token = process.env.ULTRON_WORKER_TOKEN;
  const parent = Number(process.env.ULTRON_WORKER_PARENT);
  if (!origin || !token || !Number.isInteger(parent) || parent <= 0) throw new Error('Worker configuration missing.');
  const url = new URL('/api/agent/worker', origin);
  if (url.protocol !== 'http:' || !['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)) throw new Error('Worker must connect locally.');
  let failures = 0;
  while (failures < 10) {
    // Exit when the owning server is gone rather than leaving a hidden worker running indefinitely.
    try { process.kill(parent, 0); } catch { return; }
    try {
      const response = await fetch(url, { method: 'POST', headers: { authorization: `Bearer ${token}` }, signal: AbortSignal.timeout(180000) });
      await response.arrayBuffer();
      if (response.status === 401 || response.status === 403) return;
      if (!response.ok) throw new Error('Dispatch failed.');
      failures = 0;
    } catch { failures += 1; }
    await delay(Math.min(15000, 2000 * (failures + 1)));
  }
}
run().catch(() => { process.exitCode = 1; });
