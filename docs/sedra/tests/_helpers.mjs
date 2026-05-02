// Shared utilities for SEDRA browser tests.
//
// Each *.test.mjs file imports `setup` to get a fresh page already
// pointed at the editor, and `assert*` / `summary` to record + report
// pass/fail. The runner (run.mjs) starts a python http.server before
// invoking node on each test file and exposes its URL via the
// SEDRA_TEST_URL environment variable; if that variable is unset (the
// test was launched manually against an externally-running dev
// server) we fall back to http://localhost:8766/sedra/index.html.

import { existsSync } from 'node:fs';

const DEFAULT_URL = 'http://localhost:8766/sedra/index.html';

// Locate a Chrome / Chromium binary. CI ubuntu-latest has Chrome at
// `/usr/bin/google-chrome`; the local dev path matches. Override
// with `CHROME_PATH=/path/to/chrome npm test` for any other system.
function findChrome() {
  const candidates = [
    process.env.CHROME_PATH,
    process.env.PUPPETEER_EXECUTABLE_PATH,
    '/usr/bin/google-chrome',
    '/usr/bin/chromium',
    '/usr/bin/chromium-browser',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/Applications/Chromium.app/Contents/MacOS/Chromium',
  ].filter(Boolean);
  for (const p of candidates) {
    if (p && existsSync(p)) return p;
  }
  throw new Error(
    'No Chrome/Chromium binary found. Set CHROME_PATH=/path/to/chrome ' +
    '(or PUPPETEER_EXECUTABLE_PATH) or install google-chrome / chromium ' +
    'in one of the standard locations searched above.');
}

// Launch a fresh headless browser and return a page already loaded
// against the editor with localStorage cleared and the runtime
// symbols ready. Caller is responsible for `await browser.close()`.
export async function setup({ viewport = { width: 1400, height: 900 } } = {}) {
  const { default: puppeteer } = await import('puppeteer-core');
  const browser = await puppeteer.launch({
    executablePath: findChrome(),
    headless: 'new',
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', (err) => errors.push('pageerror: ' + err.message));
  page.on('console', (msg) => {
    // Ignore 404s from optional <script> tags (MathJax / pyodide
    // load lazily and aren't required for the tests we run).
    if (msg.type() === 'error' && !msg.text().includes('404')) {
      errors.push('console: ' + msg.text());
    }
  });
  page.on('dialog', (d) => d.dismiss().catch(() => {}));
  await page.setViewport(viewport);
  const url = process.env.SEDRA_TEST_URL || DEFAULT_URL;
  await page.goto(url, { waitUntil: 'networkidle0' });
  // Make sure each test starts from a clean slate — localStorage
  // would otherwise replay the previous test's circuit.
  await page.evaluate(() => { localStorage.clear(); });
  await page.reload({ waitUntil: 'networkidle0' });
  await page.waitForFunction(
    'typeof state !== "undefined" && typeof addPart === "function" && glyphsReady',
    { timeout: 5000 });
  return { browser, page, errors };
}

// ---------------- assertion API ----------------
//
// Writes a check-result line to stdout per assertion (`✓`/`✗`) and
// keeps a tally. `summary()` prints the totals and either resolves
// to `0` (pass) or rejects with `1` (fail) — the test file should
// `process.exit(await summary())` so the runner picks up the code.

let passed = 0;
let failed = 0;
const failureMessages = [];

export function assert(cond, msg) {
  if (cond) { console.log('  ✓', msg); passed++; }
  else      { console.log('  ✗', msg); failed++; failureMessages.push(msg); }
}

export function assertEqual(actual, expected, msg) {
  const a = JSON.stringify(actual);
  const b = JSON.stringify(expected);
  if (a === b) {
    console.log('  ✓', msg);
    passed++;
  } else {
    console.log('  ✗', msg);
    console.log('      expected: ' + b);
    console.log('      actual:   ' + a);
    failed++;
    failureMessages.push(msg);
  }
}

export function summary(extraErrors = []) {
  for (const e of extraErrors) {
    console.log('  ✗ console error:', e);
    failed++;
    failureMessages.push('page error: ' + e);
  }
  console.log(`\n  ${passed} passed, ${failed} failed`);
  return failed > 0 ? 1 : 0;
}

// Convenience formatter for wire polylines.
export const fmtWire = (w) =>
  `[${w.points.map(p => `(${p[0]},${p[1]})`).join(' → ')}]`;
