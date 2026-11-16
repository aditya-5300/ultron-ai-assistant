const { performance } = require('node:perf_hooks');

function traceTransactions(db) {
  const original = db.exec.bind(db);
  let current = null;
  const finished = [];
  db.exec = sql => {
    if (sql === 'BEGIN IMMEDIATE') {
      const before = performance.now();
      try {
        const result = original(sql);
        const acquired = performance.now();
        current = { acquired, waitMs: acquired - before };
        return result;
      } catch (error) {
        finished.push({ waitMs: performance.now() - before, heldMs: null });
        throw error;
      }
    }
    const result = original(sql);
    if (current && (sql === 'COMMIT' || sql === 'ROLLBACK')) {
      finished.push({ waitMs: current.waitMs, heldMs: performance.now() - current.acquired });
      current = null;
    }
    return result;
  };
  return finished;
}

module.exports = { traceTransactions };
