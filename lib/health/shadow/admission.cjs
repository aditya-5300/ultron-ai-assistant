const { performance } = require('node:perf_hooks');
const { setTimeout: wait } = require('node:timers/promises');
const { ShadowError } = require('./contract.cjs');

const MAX_DEFERRAL_MS = 250;
const RETRY_MS = 4;

function markDeterministic(signal, active) {
  if (signal) Atomics.store(new Int32Array(signal), 0, Number(active));
}

function writerAdmission(signal) {
  const state = new Int32Array(signal);
  const idle = () => Atomics.load(state, 0) === 0;
  return {
    idle,
    async wait() {
      if (idle()) return 0;
      const start = performance.now();
      const deadline = start + MAX_DEFERRAL_MS;
      do {
        await wait(Math.min(RETRY_MS, Math.max(1, deadline - performance.now())));
        const now = performance.now();
        if (now >= deadline) throw new ShadowError('WRITER_DEFERRED');
        if (idle()) return now - start;
      } while (true);
    }
  };
}

module.exports = { markDeterministic, writerAdmission, MAX_DEFERRAL_MS, RETRY_MS };
