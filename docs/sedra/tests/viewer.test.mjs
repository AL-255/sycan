// Embeddable view-only viewer (viewer.html + viewer.ts) and the
// editor-side sharing surface: base64 codec round-trip, copy-embed
// helpers, and #data= hash import.
import { setup, assert, summary } from './_helpers.mjs';

const BASE = (process.env.SEDRA_TEST_URL || 'http://localhost:8766/sedra/index.html');
const viewerUrl = (suffix) => BASE.replace(/index\.html$/, 'viewer.html') + suffix;

// A canonical-form voltage divider: R1 — W1(T-vertex) — R2 — W3 — GND,
// with a labelled dangling tap W2 off W1's midpoint vertex.
const DOC = {
  version: 1,
  parts: [
    { id: 'R1', type: 'res', x: 0,   y: 0,  rot: 90, value: 'R1' },
    { id: 'R2', type: 'res', x: 200, y: 0,  rot: 90, value: 'R2' },
    { id: 'G1', type: 'gnd', x: 240, y: 80, rot: 0 },
  ],
  wires: [
    { id: 'W1', points: [[40, 0], [100, 0], [160, 0]] },
    { id: 'W2', points: [[100, 0], [100, 80]], label: 'mid' },
    { id: 'W3', points: [[240, 0], [240, 80]] },
  ],
  nameCounters: { R: 2, GND: 1 },
};
const B64 = Buffer.from(JSON.stringify(DOC)).toString('base64url');

// ---- 1. viewer renders a base64 circuit --------------------------
const { browser, page, errors } = await setup({
  page: 'viewer.html',
  hash: `#data=${B64}`,
  waitFor: 'window.sedraViewer && sedraViewer.ready',
});

const scene = await page.evaluate(() => ({
  glyphs: document.querySelectorAll('#viewer-svg .glyph').length,
  names: [...document.querySelectorAll('#viewer-svg .part-name')]
    .map(t => t.textContent).sort(),
  wires: document.querySelectorAll('#viewer-svg path.wire').length,
  dots: [...document.querySelectorAll('#viewer-svg .node-dot')]
    .map(c => `${c.getAttribute('cx')},${c.getAttribute('cy')}`),
  openRings: document.querySelectorAll('#viewer-svg .terminal-open').length,
  labels: [...document.querySelectorAll('#viewer-svg .net-label-text')]
    .map(t => t.textContent),
  labelBgs: document.querySelectorAll('#viewer-svg .net-label-bg').length,
  msgHidden: document.getElementById('viewer-msg').hidden,
  openHref: document.getElementById('v-open').getAttribute('href'),
}));
assert(scene.glyphs === 3, `all 3 part glyphs render (got ${scene.glyphs})`);
assert(JSON.stringify(scene.names) === '["R1","R2"]',
       `part names render, gnd refdes suppressed (got ${JSON.stringify(scene.names)})`);
assert(scene.wires === 3, `all 3 wires render (got ${scene.wires})`);
assert(JSON.stringify(scene.dots) === '["100,0"]',
       `junction dot at the T only (got ${JSON.stringify(scene.dots)})`);
assert(scene.openRings === 1,
       `exactly one open-terminal ring — R1's left pin (got ${scene.openRings})`);
assert(JSON.stringify(scene.labels) === '["mid"]' && scene.labelBgs === 1,
       `net label tag renders (got ${JSON.stringify(scene.labels)})`);
assert(scene.msgHidden, 'no error card on a valid circuit');
assert(scene.openHref === `index.html#data=${B64}`,
       'Open-in-SEDRA link carries the same payload');

// ---- 2. fit covers the content; zoom API works -------------------
const cam = await page.evaluate(() => {
  const vb = document.getElementById('viewer-svg')
    .getAttribute('viewBox').split(' ').map(Number);
  const z0 = sedraViewer.zoom();
  sedraViewer.setZoom(z0 * 2);
  const z1 = sedraViewer.zoom();
  sedraViewer.fit();
  return { vb, z0, z1, zFit: sedraViewer.zoom() };
});
// Content bbox is roughly x ∈ [-42, 242], y ∈ [-42, 96].
assert(cam.vb[0] <= -42 && cam.vb[0] + cam.vb[2] >= 242
    && cam.vb[1] <= -42 && cam.vb[1] + cam.vb[3] >= 96,
       `initial fit covers the schematic (viewBox ${cam.vb.join(' ')})`);
assert(Math.abs(cam.z1 - cam.z0 * 2) < 1e-9, 'setZoom doubles the zoom');
assert(Math.abs(cam.zFit - cam.z0) < 1e-9, 'fit() restores the fitted zoom');

// ---- 3. URL options: forced theme + hidden controls --------------
await page.goto(viewerUrl(`?theme=dark&controls=0#data=${B64}`),
                { waitUntil: 'networkidle0' });
await page.waitForFunction('window.sedraViewer && sedraViewer.ready');
const opts = await page.evaluate(() => ({
  theme: document.documentElement.dataset.theme,
  controlsHidden: document.getElementById('viewer-controls').hidden,
}));
assert(opts.theme === 'dark', 'theme=dark forces the dark palette');
assert(opts.controlsHidden, 'controls=0 hides the control cluster');

// ---- 4. graceful failure: garbage payload / no payload -----------
await page.goto(viewerUrl('#data=%%%not-base64%%%'), { waitUntil: 'networkidle0' });
await page.waitForFunction('window.sedraViewer && sedraViewer.ready');
const bad = await page.evaluate(() => ({
  msgShown: !document.getElementById('viewer-msg').hidden,
  glyphs: document.querySelectorAll('#viewer-svg .glyph').length,
  controlsHidden: document.getElementById('viewer-controls').hidden,
}));
assert(bad.msgShown && bad.glyphs === 0 && bad.controlsHidden,
       'malformed payload shows the error card instead of crashing');

await page.goto(viewerUrl(''), { waitUntil: 'networkidle0' });
await page.waitForFunction('window.sedraViewer && sedraViewer.ready');
const empty = await page.evaluate(
  () => !document.getElementById('viewer-msg').hidden);
assert(empty, 'missing payload shows the no-data card');

await browser.close();

// ---- 5. editor side: codec round-trip + share commands -----------
const ed = await setup();
const share = await ed.page.evaluate(() => {
  addPart('res', 0, 0, 90);
  addPart('cap', 200, 0, 90);
  state.wires.push({ id: 'W1', points: [[40, 0], [160, 0]], label: 'out' });
  pushHistory();
  const doc = { version: 1, parts: state.parts, wires: state.wires,
                nameCounters: state.nameCounters };
  const rt = decodeCircuitB64(encodeCircuitB64(doc));
  return {
    rtLabel: rt && rt.wires[0].label,
    url: viewerShareUrl(),
    snippet: buildEmbedSnippet(),
    cmds: COMMANDS.filter(c => c.id === 'file.copyEmbed'
                            || c.id === 'file.copyViewLink')
      .map(c => ({ id: c.id, enabled: !c.enabled || c.enabled(makeCtx([0, 0], null, 'palette')) })),
  };
});
assert(share.rtLabel === 'out',
       `encode/decode round-trips the circuit (label → ${share.rtLabel})`);
assert(/viewer\.html#data=[A-Za-z0-9_-]+$/.test(share.url),
       `share URL points at viewer.html with URL-safe b64 (${share.url.slice(0, 60)}…)`);
assert(share.snippet.startsWith('<iframe src="')
    && share.snippet.includes('viewer.html#data=')
    && share.snippet.includes('title="SEDRA schematic (view-only)"'),
       'embed snippet is a self-contained iframe');
assert(share.cmds.length === 2 && share.cmds.every(c => c.enabled),
       `both share commands registered + enabled (${JSON.stringify(share.cmds)})`);

// Unicode survives the codec (μ in a value).
const uni = await ed.page.evaluate(() => {
  const doc = { version: 1,
                parts: [{ id: 'C1', type: 'cap', x: 0, y: 0, rot: 0, value: '1μF' }],
                wires: [] };
  return decodeCircuitB64(encodeCircuitB64(doc)).parts[0].value;
});
assert(uni === '1μF', `unicode values survive the codec (got ${uni})`);

// ---- 6. editor hash import (#data=…) ------------------------------
const SHARE_B64 = await ed.page.evaluate(() =>
  viewerShareUrl().split('#data=')[1]);
await ed.page.evaluate(() => localStorage.clear());
await ed.page.goto('about:blank');
await ed.page.goto(`${BASE}#data=${SHARE_B64}`, { waitUntil: 'networkidle0' });
await ed.page.waitForFunction(
  'typeof state !== "undefined" && glyphsReady && state.parts.length > 0',
  { timeout: 5000 });
const imported = await ed.page.evaluate(() => ({
  ids: state.parts.map(p => p.id).sort(),
  wires: state.wires.length,
  hash: location.hash,
}));
assert(JSON.stringify(imported.ids) === '["C1","R1"]',
       `editor imports the linked schematic (got ${JSON.stringify(imported.ids)})`);
assert(imported.wires === 1, 'linked wires import too');
assert(imported.hash === '', 'fragment is stripped after import');

await ed.browser.close();
process.exit(summary(errors.concat(ed.errors)));
