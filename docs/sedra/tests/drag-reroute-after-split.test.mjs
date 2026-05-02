// Regression: when a partial-segment box-select is dragged, the
// unselected sub-wires that ``splitPartialWires`` peels off must
// auto-reroute so their split-boundary endpoint follows the moved
// pieces. Previously the spanning-detection only anchored on
// selected *part* terminals — the boundary points where a wire was
// split off were ignored, so the unselected remainder stayed put,
// connectivity changed, and the drag was reverted by the parity
// check (visually: nothing happened).
//
// Loads the bundled circuit fixture, box-selects (500,420)→(380,220)
// (which picks R2, R3, all of W2/W4 and the right halves of W1/W3),
// then drives a 40-px right drag.
import { setup, assert, summary } from './_helpers.mjs';
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
  // Drag-mode is what enables spanning-wire capture / reroute. The
  // parity check is what would (incorrectly) revert the drag if the
  // reroute were still missing — leaving it on means a green test
  // is also a positive proof that connectivity stayed intact.
  document.getElementById('drag-mode').checked = true;
  document.getElementById('parity-check').checked = true;
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

await page.mouse.move(sx(500), sy(420));
await page.mouse.down();
await page.mouse.move(sx(380), sy(220), { steps: 6 });
await page.mouse.up();

const result = await page.evaluate(() => {
  // Drive the drag programmatically so we can inspect the orig-kind
  // bucketing without racing the mouse-event timing. This is the
  // pattern used by the other drag tests.
  const ids = [...state.selectedIds];
  startMove(ids, [400, 320], /*viaDrag=*/true, /*freshlyPasted=*/false);
  const origKinds = Object.fromEntries(
    [...state.moveDraft.origs.entries()].map(([id, o]) => [id, o.kind])
  );
  updateMove([440, 320]);   // delta = (40, 0)
  commitMove();
  return {
    origKinds,
    parts: Object.fromEntries(
      state.parts.map(p => [p.id, [p.x, p.y]])),
    wires: state.wires.map(w => ({ id: w.id, points: w.points })),
  };
});

// ---- Origs classification -----------------------------------------

// The selected parts and the "selected pieces" of W1 / W3 (renamed
// W11 / W13 by splitPartialWires) come through as plain part / wire
// translates. W2 and W4 are fully selected (every segment marked) so
// they also translate as wholes.
assert(result.origKinds.R2 === 'part' && result.origKinds.R3 === 'part',
       `selected parts come through as 'part' origs`);
const wireKinds = Object.entries(result.origKinds)
  .filter(([id, _]) => id.startsWith('W'))
  .reduce((acc, [id, k]) => {
    (acc[k] = acc[k] || []).push(id);
    return acc;
  }, {});
assert((wireKinds['wire'] || []).length === 4,
       `4 selected sub-wires translate (W2, W4, and the selected halves of W1, W3); ` +
       `got ${JSON.stringify(wireKinds['wire'])}`);
assert((wireKinds['wire-spanning'] || []).length === 2,
       `the unselected sub-wires of W1 and W3 must be captured as ` +
       `wire-spanning so they reroute (got ${JSON.stringify(wireKinds['wire-spanning'])})`);

// ---- Geometry ------------------------------------------------------

assert(JSON.stringify(result.parts.R2) === '[440,320]',
       `R2 moved 40px right (got ${JSON.stringify(result.parts.R2)})`);
assert(JSON.stringify(result.parts.R3) === '[520,320]',
       `R3 moved 40px right (got ${JSON.stringify(result.parts.R3)})`);

// Every part terminal must still have at least one wire-vertex on it
// after the drag — that's what the parity check measures, and it
// directly reflects whether auto-rerouting closed the gap.
const vertSet = new Set(result.wires.flatMap(w =>
  w.points.map(([x, y]) => `${x},${y}`)));
const expectedTerminals = [
  [440, 280],  // R2 top
  [440, 360],  // R2 bottom
  [520, 280],  // R3 top
  [520, 360],  // R3 bottom
  [320, 280],  // R1 top  (must still be wired through W10)
  [320, 360],  // R1 bottom
];
for (const [tx, ty] of expectedTerminals) {
  assert(vertSet.has(`${tx},${ty}`),
         `terminal (${tx}, ${ty}) still has a wire vertex on it after drag`);
}

// The unselected halves of W1 and W3 (W10 and W12 with default id
// allocation) should each have their split-boundary endpoint
// rerouted to the new x = 440 column.
const w10 = result.wires.find(w => w.id === 'W10');
const w12 = result.wires.find(w => w.id === 'W12');
assert(w10 && w10.points[w10.points.length - 1][0] === 440,
       `W10 (unselected half of W1) reroutes its boundary endpoint to x=440 ` +
       `(got ${w10 ? JSON.stringify(w10.points) : 'missing'})`);
assert(w12 && w12.points[w12.points.length - 1][0] === 440,
       `W12 (unselected half of W3) reroutes its boundary endpoint to x=440 ` +
       `(got ${w12 ? JSON.stringify(w12.points) : 'missing'})`);

await browser.close();
process.exit(summary(errors));
