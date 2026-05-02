// Regression for the exact user-reported scenario: load the
// bundled fixture circuit, box-select (500,420)→(380,220), then
// click (440,400). The click lands on segment 0 of W4 — a wire
// whose every vertex was inside the box, so every one of its
// segments is in ``selectedSegments``. Because the unit of
// selection is always the line segment (no special whole-wire
// state), clicking on a segment that's already marked must not
// change the selection — the click only initiates a drag.
import { setup, summary, assert } from './_helpers.mjs';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const json = readFileSync(join(here, 'fixtures', 'circuit-1.json'), 'utf8');

const { browser, page, errors } = await setup();

await page.evaluate((jsonText) => {
  const data = JSON.parse(jsonText);
  state.parts = data.parts || [];
  state.wires = data.wires || [];
  state.nameCounters = data.nameCounters || {};
  state.nextId = data.parts.length + data.wires.length + 1;
  state.selectedIds.clear();
  state.selectedSegments.clear();
  state.pan = { x: 80, y: 80 };
  state.zoom = 1;
  state.tool = 'select';
  pushHistory();
  render();
}, json);

const wb = await page.evaluate(() => {
  const w = document.getElementById('canvas-wrap');
  const r = w.getBoundingClientRect();
  return { left: r.left, top: r.top };
});
const pz = await page.evaluate(() => ({
  px: state.pan.x, py: state.pan.y, z: state.zoom,
}));
const sx = (x) => wb.left + pz.px + x * pz.z;
const sy = (y) => wb.top + pz.py + y * pz.z;

// Box-select (500,420) → (380,220).
await page.mouse.move(sx(500), sy(420));
await page.mouse.down();
await page.mouse.move(sx(380), sy(220), { steps: 6 });
await page.mouse.up();

const before = await page.evaluate(() => ({
  ids: [...state.selectedIds].sort(),
  segs: [...state.selectedSegments].sort(),
}));

// W4's two segments and W2's one segment are wholly enclosed; W1
// contributes its top-bus right half (W1|2 and W1|3); W3 contributes
// its right vertical (W3|2); R2 and R3 are the two enclosed parts.
assert(JSON.stringify(before.ids) === '["R2","R3","W1","W2","W3","W4"]',
       `box covers the right cluster (got ${JSON.stringify(before.ids)})`);
assert(before.segs.includes('W4|0') && before.segs.includes('W4|1'),
       `W4 has every segment marked (got ${JSON.stringify(before.segs)})`);
assert(before.segs.includes('W3|2') && before.segs.includes('W1|2')
       && before.segs.includes('W1|3') && before.segs.includes('W2|0'),
       `partial-wires keep their per-segment markers`);

// Click at (440, 400) — squarely on W4 segment 0.
await page.mouse.move(sx(440), sy(400));
await page.mouse.down();
await page.mouse.up();

const after = await page.evaluate(() => ({
  ids: [...state.selectedIds].sort(),
  segs: [...state.selectedSegments].sort(),
}));
assert(JSON.stringify(after.ids) === JSON.stringify(before.ids),
       `click on already-selected wire keeps selectedIds intact`);
assert(JSON.stringify(after.segs) === JSON.stringify(before.segs),
       `click on a marked segment must not mutate the selection ` +
       `(got ${JSON.stringify(after.segs)}, expected ` +
       `${JSON.stringify(before.segs)})`);

await browser.close();
process.exit(summary(errors));
