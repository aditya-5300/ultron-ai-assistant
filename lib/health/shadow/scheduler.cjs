const path = require('node:path');
const { Worker } = require('node:worker_threads');

function scheduler({ start, now = Date.now, spacingMs = 300000 }) {
  let worker, active = false, closed = false, failures = 0, next = 0;
  function settle(result) {
    active = false;
    failures = result.ok || !result.transient ? 0 : failures + 1;
    next = now() + (failures >= 3 ? Math.min(1800000, spacingMs * 2 ** Math.min(failures - 2, 4)) : spacingMs);
  }
  return {
    submit(id) {
      if (closed || active || now() < next) return false;
      try {
        if (!worker) {
          const thread = start();
          worker = thread;
          const lost = () => {
            if (worker !== thread) return;
            worker = null;
            settle({ transient: true });
          };
          thread.on('message', result => { if (worker === thread && !closed) settle(result); });
          thread.on('error', lost);
          thread.on('exit', lost);
        }
        active = true;
        worker.postMessage(id);
        return true;
      } catch {
        settle({ transient: true });
        return false;
      }
    },
    async close() {
      closed = true;
      if (worker) await worker.terminate().catch(() => {});
    }
  };
}

function createShadowScheduler(root, env = process.env) {
  if (env.ULTRON_INFERENCE_MODE !== 'shadow') return null;
  const port = env.ULTRON_INFERENCE_PORT ?? '8765';
  return scheduler({ start: () => new Worker(path.join(__dirname, 'thread.cjs'), {
    env: {},
    workerData: { path: path.join(root, '.ultron', 'health.sqlite'), config: {
      port: /^\d{4,5}$/.test(port) ? Number(port) : null, token: env.ULTRON_INFERENCE_TOKEN
    } }
  }) });
}

module.exports = { createShadowScheduler, scheduler };
