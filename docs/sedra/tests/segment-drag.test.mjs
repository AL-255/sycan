// Smallest unit of operation = one line segment, also for dragging.
//
// Regression: with the bundled circuit-1 fixture, box-selecting
// (500,420)→(380,220) and dragging the selection should move only
// the *selected* segments. W1's segments 0–1 (its left half) and
// W3's segments 0–1 (its left half) stay anchored at their pre-drag
// coordinates — only their right halves, which were strictly inside
// the box, follow the drag delta.
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
  // Drag-mode off so the test exercises the segment-split path
  // alone, not the part-terminal capture/spanning logic on top.
  document.getElementById('drag-mode').checked = false;
  document.getElementById('parity-check').checked = false;
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
const sy = (y) => wb.top  + pz.py + y * pz.z;

// 1) Box-select (500,420) → (380,220).
await page.mouse.move(sx(500), sy(420));
await page.mouse.down();
await page.mouse.move(sx(380), sy(220), { steps: 6 });
await page.mouse.up();

const sel = await page.evaluate(() => ({
  ids: [...state.selectedIds].sort(),
  segs: [...state.selectedSegments].sort(),
}));
assert(sel.segs.includes('W1|2') && sel.segs.includes('W1|3')
       && !sel.segs.includes('W1|0') && !sel.segs.includes('W1|1'),
       `box-select picks W1's right half only ` +
       `(got ${JSON.stringify(sel.segs)})`);
assert(sel.segs.includes('W3|2') && !sel.segs.includes('W3|0')
       && !sel.segs.includes('W3|1'),
       `box-select picks W3's right vertical only`);

// Snapshot the original (pre-drag) wire vertex coordinates as a
// flat string set so we can ask "is this point still on disk after
// the drag?"
function pointsSet(arr) {
  return new Set(arr.flatMap(w => w.points.map(p => `${p[0]},${p[1]}`)));
}

// 2) Drag R3 down-and-right by (40, 80). R3 is at (480, 320) so
//    we click on R3's centre.
await page.mouse.move(sx(480), sy(320));
await page.mouse.down();
await page.mouse.move(sx(480 + 40), sy(320 + 80), { steps: 6 });
await page.mouse.up();

const wiresAfter = await page.evaluate(() =>
  state.wires.map(w => ({ id: w.id, points: w.points }))
);
const partsAfter = await page.evaluate(() =>
  state.parts.map(p => ({ id: p.id, x: p.x, y: p.y }))
);
const ptSet = pointsSet(wiresAfter);

// 3) Selected parts moved by the delta.
const R2 = partsAfter.find(p => p.id === 'R2');
const R3 = partsAfter.find(p => p.id === 'R3');
assert(R2 && R2.x === 440 && R2.y === 400,
       `R2 moved by (40, 80) (got (${R2 && R2.x}, ${R2 && R2.y}))`);
assert(R3 && R3.x === 520 && R3.y === 400,
       `R3 moved by (40, 80) (got (${R3 && R3.x}, ${R3 && R3.y}))`);

// 4) UNSELECTED segments of W1 stay anchored — every vertex of the
//    left half ((320,280), (320,240), (400,240)) is still on disk.
//    The point (400,240) is the boundary; W1's left piece keeps it
//    as its own free endpoint.
for (const p of [[320, 280], [320, 240], [400, 240]]) {
  assert(ptSet.has(`${p[0]},${p[1]}`),
         `W1 unselected vertex (${p[0]}, ${p[1]}) survives the drag`);
}

// 5) UNSELECTED segments of W3 stay anchored. W3's left piece's
//    boundary at (400, 400) likewise survives.
for (const p of [[320, 360], [320, 400], [400, 400]]) {
  assert(ptSet.has(`${p[0]},${p[1]}`),
         `W3 unselected vertex (${p[0]}, ${p[1]}) survives the drag`);
}

// 6) SELECTED portions moved by the delta. W1's right half went
//    from (480,240)→(480,280) to (520,320)→(520,360); W4's vertices
//    similarly translate.
for (const p of [[440, 320], [520, 320], [520, 360]]) {
  assert(ptSet.has(`${p[0]},${p[1]}`),
         `W1 selected vertex translates to (${p[0]}, ${p[1]})`);
}
for (const p of [[440, 480], [520, 480], [520, 440]]) {
  assert(ptSet.has(`${p[0]},${p[1]}`),
         `W4 vertex translates to (${p[0]}, ${p[1]})`);
}

// 7) The original right-half coordinates of W1 and W4 must NOT
//    survive at their pre-drag positions (because they moved
//    away). (440, 240) and (480, 280) were the moved endpoints —
//    they shouldn't appear unmoved anywhere.
for (const p of [[480, 240], [480, 280], [480, 400], [480, 360]]) {
  assert(!ptSet.has(`${p[0]},${p[1]}`),
         `pre-drag selected vertex (${p[0]}, ${p[1]}) is gone`);
}

// 8) GND-side wire W5 was entirely outside the box → not in the
//    selection → unmoved. Its endpoints (320, 440) and (320, 400)
//    survive.
for (const p of [[320, 440], [320, 400]]) {
  assert(ptSet.has(`${p[0]},${p[1]}`),
         `W5 vertex (${p[0]}, ${p[1]}) untouched (not in selection)`);
}

await browser.close();
process.exit(summary(errors));
