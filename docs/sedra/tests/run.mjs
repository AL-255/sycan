// SEDRA test runner.
//
// 1. (Re)compiles TypeScript sources if they're newer than the
//    emitted JS — keeps `npm test` reliable without a separate
//    `npm run build` step.
// 2. Spawns a python http.server pointed at `docs/` so the browser
//    tests can load `/sedra/index.html` and the surrounding repl/res
//    glyphs.
// 3. Runs every `*.test.mjs` in this directory (in lex order) under
//    its own `node` process so a crash in one test doesn't poison
//    the others.
// 4. Tears down the server and exits non-zero if any test failed.
//
// Use `SEDRA_TEST_PORT=N npm test` to pick a specific port.
//
// Manual run against an externally-running server:
//   ./run_webpage.sh --sedra
//   SEDRA_TEST_URL=http://localhost:8766/sedra/index.html \
//     node docs/sedra/tests/segment-select.test.mjs

import { spawn, spawnSync } from 'node:child_process';
import { readdirSync, statSync } from 'node:fs';
import { join, resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as sleep } from 'node:timers/promises';

const __dirname = dirname(fileURLToPath(import.meta.url));
const sedraDir = resolve(__dirname, '..');
const docsDir = resolve(sedraDir, '..');
const PORT = process.env.SEDRA_TEST_PORT || '8766';
const BASE_URL = `http://localhost:${PORT}/sedra/index.html`;

// ---- 1. tsc compile if needed -----------------------------------
function newestMtime(dir, predicate) {
  let best = 0;
  for (const ent of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, ent.name);
    if (ent.isDirectory()) {
      best = Math.max(best, newestMtime(p, predicate));
    } else if (predicate(p)) {
      best = Math.max(best, statSync(p).mtimeMs);
    }
  }
  return best;
}
function fileMtime(p) {
  try { return statSync(p).mtimeMs; } catch (_) { return 0; }
}
const srcMax = newestMtime(join(sedraDir, 'src'), (p) => p.endsWith('.ts'));
const outMin = Math.min(
  fileMtime(join(sedraDir, 'glyphs.js')),
  fileMtime(join(sedraDir, 'editor.js')),
);
if (srcMax === 0 || outMin === 0 || srcMax > outMin) {
  console.log('==> tsc (sources newer than emitted JS)');
  const r = spawnSync('npx', ['--no-install', 'tsc'],
                      { cwd: sedraDir, stdio: 'inherit' });
  if (r.status !== 0) {
    console.error('tsc failed; aborting test run');
    process.exit(r.status ?? 1);
  }
}

// ---- 2. start http.server ---------------------------------------
console.log(`==> python -m http.server --directory ${docsDir} ${PORT}`);
const server = spawn('python3',
  ['-m', 'http.server', '--directory', docsDir, '--bind', '127.0.0.1', PORT],
  { stdio: ['ignore', 'pipe', 'pipe'] });
const serverLog = [];
server.stdout.on('data', (b) => serverLog.push(String(b)));
server.stderr.on('data', (b) => serverLog.push(String(b)));

let stopped = false;
function stopServer() {
  if (stopped) return;
  stopped = true;
  try { server.kill('SIGTERM'); } catch (_) {}
}
process.on('exit', stopServer);
process.on('SIGINT',  () => { stopServer(); process.exit(130); });
process.on('SIGTERM', () => { stopServer(); process.exit(143); });

// Wait until the server answers an actual request.
async function waitForServer() {
  for (let i = 0; i < 40; i++) {
    try {
      const r = await fetch(BASE_URL);
      if (r.ok) return;
    } catch (_) { /* not yet ready */ }
    await sleep(150);
  }
  console.error('http.server failed to start; recent log:');
  console.error(serverLog.join(''));
  throw new Error('server timeout');
}
await waitForServer();

// ---- 3. run each test -------------------------------------------
const tests = readdirSync(__dirname)
  .filter(f => f.endsWith('.test.mjs'))
  .sort();
if (tests.length === 0) {
  console.error('no tests found under', __dirname);
  stopServer();
  process.exit(1);
}

let failed = 0;
for (const name of tests) {
  const t0 = Date.now();
  console.log(`\n==> ${name}`);
  const code = await new Promise(resolveExit => {
    const child = spawn('node', [join(__dirname, name)], {
      stdio: 'inherit',
      env: { ...process.env, SEDRA_TEST_URL: BASE_URL },
    });
    child.on('exit', (c) => resolveExit(c ?? 1));
  });
  const dt = ((Date.now() - t0) / 1000).toFixed(1);
  if (code === 0) console.log(`    ${name} passed (${dt}s)`);
  else            { console.log(`    ${name} FAILED (${dt}s, exit ${code})`); failed++; }
}

// ---- 4. teardown + result ---------------------------------------
stopServer();
console.log('');
if (failed > 0) {
  console.error(`${failed} test file(s) failed.`);
  process.exit(1);
}
console.log(`All ${tests.length} test file(s) passed.`);
