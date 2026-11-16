const fs = require('node:fs');
const path = require('node:path');
const { spawn } = require('node:child_process');
const { once } = require('node:events');
const readline = require('node:readline');
const { fixture, repo, service } = require('./phase8b-fixture.cjs');

async function main() {
  if (!process.env.ULTRON_PHASE8B_PYTHON) throw new Error('Set ULTRON_PHASE8B_PYTHON to the test interpreter');
  const context = { after(cleanup) { this.cleanup = cleanup; } };
  const root = fixture(context);
  let child;
  try {
    const python = await service(root);
    await python.start();
    const ack = path.join(root.root, 'restart.ack');
    child = spawn(process.execPath, ['--test', '--test-concurrency=1', '--test-reporter=tap',
      'scripts/phase7-shadow.test.cjs', 'scripts/phase8-shadow.test.cjs'], {
      cwd: repo, windowsHide: true, stdio: ['ignore', 'pipe', 'inherit'],
      env: { ...process.env, ULTRON_INFERENCE_TOKEN: python.token,
        ULTRON_PHASE7_FIXTURE_PORT: String(python.port), ULTRON_PHASE7_RESTART_ACK: ack }
    });
    const exited = once(child, 'exit');
    for await (const line of readline.createInterface({ input: child.stdout })) {
      if (line.includes('PHASE7_RESTART_LISTENER')) {
        await python.stop();
        await python.start();
        fs.writeFileSync(ack, '');
      }
      process.stdout.write(line + '\n');
    }
    const [code] = await exited;
    process.exitCode = code ?? 1;
  } finally {
    if (child) await root.stop(child);
    await context.cleanup();
  }
}

main().catch(error => { console.error(error.message); process.exitCode = 1; });
