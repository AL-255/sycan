// Segment-level delete: pressing Delete with one or more wire
// segments marked must remove only those segments and split the
// host wire into sub-wires for each contiguous run of unselected
// segments. A wire whose every segment is marked vanishes
// entirely; a wire with a single contiguous chunk removed at the
// head or tail simply shrinks.
import { setup, summary, assert } from './_helpers.mjs';

const { browser, page, errors } = await setup();

async function reset() {
  await page.evaluate(() => {
    state.parts = []; state.wires = []; state.nameCounters = {};
    state.nextId = 1;
    state.selectedIds.clear();
    state.selectedSegments.clear();
    state.pan = { x: 80, y: 200 }; state.zoom = 1;
    state.tool = 'select';
  });
}

async function pressDelete() {
  await page.evaluate(() => {
    document.dispatchEvent(new KeyboardEvent('keydown', {
      key: 'Delete', bubbles: true,
    }));
  });
}

// (1) Single segment in the middle of a 3-segment wire splits the
// wire into two pieces — the head [(0,0),(80,0)] and the tail
// [(80,-80),(160,-80)] — leaving a gap where the deleted vertical
// used to be.
await reset();
await page.evaluate(() => {
  state.wires.push({ id: 'W1',
    points: [[0, 0], [80, 0], [80, -80], [160, -80]] });
  state.nextId = 2;
  state.selectedIds.add('W1');
  state.selectedSegments.add('W1|1');
  pushHistory(); render();
});
await pressDelete();
let snapshot = await page.evaluate(() => state.wires.map(w => ({
  points: w.points,
})));
assert(snapshot.length === 2,
       `1 segment removed from middle splits into 2 wires (got ${snapshot.length})`);
const polylines = snapshot.map(w => JSON.stringify(w.points)).sort();
assert(polylines[0] === JSON.stringify([[0, 0], [80, 0]]),
       `head fragment is the original first segment (got ${polylines[0]})`);
assert(polylines[1] === JSON.stringify([[80, -80], [160, -80]]),
       `tail fragment is the original last segment (got ${polylines[1]})`);

// (2) Every segment of a wire selected → wire disappears entirely.
await reset();
await page.evaluate(() => {
  state.wires.push({ id: 'W1',
    points: [[0, 0], [40, 0], [40, -40]] });
  state.nextId = 2;
  state.selectedIds.add('W1');
  state.selectedSegments.add('W1|0');
  state.selectedSegments.add('W1|1');
  pushHistory(); render();
});
await pressDelete();
snapshot = await page.evaluate(() => state.wires);
assert(snapshot.length === 0,
       `every segment selected → wire deleted entirely (got ${snapshot.length})`);

// (3) Trailing segment selected → head shrinks, no split.
await reset();
await page.evaluate(() => {
  state.wires.push({ id: 'W1',
    points: [[0, 0], [40, 0], [40, -40], [80, -40]] });
  state.nextId = 2;
  state.selectedIds.add('W1');
  state.selectedSegments.add('W1|2');  // last segment only
  pushHistory(); render();
});
await pressDelete();
snapshot = await page.evaluate(() => state.wires.map(w => w.points));
assert(snapshot.length === 1,
       `tail removed leaves one shorter wire (got ${snapshot.length})`);
assert(JSON.stringify(snapshot[0]) === JSON.stringify([[0, 0], [40, 0], [40, -40]]),
       `remaining wire has the head segments (got ${JSON.stringify(snapshot[0])})`);

// (4) Mixed deletion: parts and segments together.
await reset();
await page.evaluate(() => {
  addPart('res', 0, 0, 90);          // R1
  state.wires.push({ id: 'W1',
    points: [[40, 0], [120, 0], [120, -40]] });
  state.nextId = 2;
  state.selectedIds.add('R1');
  state.selectedIds.add('W1');
  state.selectedSegments.add('W1|0');
  pushHistory(); render();
});
await pressDelete();
const after = await page.evaluate(() => ({
  parts: state.parts.map(p => p.id),
  wires: state.wires.map(w => w.points),
}));
assert(after.parts.length === 0,
       `selected part is removed (got ${JSON.stringify(after.parts)})`);
assert(after.wires.length === 1,
       `wire shrinks to its remaining vertical segment (got ${after.wires.length})`);
assert(JSON.stringify(after.wires[0]) === JSON.stringify([[120, 0], [120, -40]]),
       `surviving fragment is the right vertical segment ` +
       `(got ${JSON.stringify(after.wires[0])})`);

await browser.close();
process.exit(summary(errors));
