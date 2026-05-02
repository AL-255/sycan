// Box-select picks segments at the level of *individual line
// segments* of a wire, not the whole polyline. A box that crosses
// through a multi-segment wire selects only the segments whose two
// endpoints fall inside the rectangle; a box that contains every
// vertex collapses to a whole-wire selection.
//
// In both cases the wire id ends up in `selectedIds` so the existing
// delete / copy / move semantics keep operating on the wire as a
// whole — partial selection only changes the *visual*.
import { setup, assert, summary } from './_helpers.mjs';

const { browser, page, errors } = await setup();

await page.evaluate(() => {
  state.parts = []; state.wires = []; state.nameCounters = {}; state.nextId = 1;
  state.selectedIds.clear();
  state.selectedSegments.clear();
  state.pan = { x: 80, y: 200 }; state.zoom = 1;
  state.tool = 'select';

  // Z-shaped wire — four segments, indices 0..3:
  //   0:   (0,0)   → (120,0)     horizontal
  //   1:   (120,0) → (120,-80)   vertical
  //   2:   (120,-80) → (240,-80) horizontal
  //   3:   (240,-80) → (240,0)   vertical
  state.wires.push({ id: 'W1',
    points: [[0, 0], [120, 0], [120, -80], [240, -80], [240, 0]] });
  // A second, simpler wire to verify the box only touches what the
  // user encloses.
  state.wires.push({ id: 'W2', points: [[400, 0], [560, 0]] });
  state.nextId = 3;
  pushHistory(); render();
});

// Convert world → screen given the current pan/zoom.
async function wrapBox() {
  return await page.evaluate(() => {
    const w = document.getElementById('canvas-wrap');
    const r = w.getBoundingClientRect();
    return { left: r.left, top: r.top };
  });
}
async function panZoom() {
  return await page.evaluate(() => ({
    px: state.pan.x, py: state.pan.y, z: state.zoom,
  }));
}
async function dragBox(wx0, wy0, wx1, wy1) {
  const wb = await wrapBox();
  const pz = await panZoom();
  const sx = (wx) => wb.left + pz.px + wx * pz.z;
  const sy = (wy) => wb.top + pz.py + wy * pz.z;
  await page.mouse.move(sx(wx0), sy(wy0));
  await page.mouse.down();
  await page.mouse.move(sx(wx1), sy(wy1), { steps: 6 });
  await page.mouse.up();
}

// (1) Box covers segments 0 and 1 — the top-left horizontal and
// the descending vertical. Segment 2 ((120,-80)→(240,-80)) is
// excluded because its right endpoint (x=240) is outside the box;
// segment 3 ((240,-80)→(240,0)) is excluded for the same reason.
await dragBox(-10, -90, 130, 10);
let s = await page.evaluate(() => ({
  ids: [...state.selectedIds].sort(),
  segs: [...state.selectedSegments].sort(),
}));
assert(JSON.stringify(s.ids) === '["W1"]',
       `partial box puts the wire id in selectedIds (got ${JSON.stringify(s.ids)})`);
assert(JSON.stringify(s.segs) === '["W1|0","W1|1"]',
       `partial box selects only segments 0+1 (got ${JSON.stringify(s.segs)})`);

// (2) Drop selection. Then a box that fully encloses W1 marks
// every segment individually — there is no whole-wire collapse.
// Visually equivalent to a continuous highlight (the per-segment
// overlays cover the full polyline), but the *state* is uniformly
// segment-level so a follow-up click never causes a visual
// transition.
await page.evaluate(() => {
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
});
await dragBox(-20, -100, 260, 20);
s = await page.evaluate(() => ({
  ids: [...state.selectedIds].sort(),
  segs: [...state.selectedSegments].sort(),
}));
assert(JSON.stringify(s.ids) === '["W1"]',
       `full-enclose box selects the wire (got ${JSON.stringify(s.ids)})`);
assert(JSON.stringify(s.segs) === '["W1|0","W1|1","W1|2","W1|3"]',
       `full-enclose box marks every segment (got ${JSON.stringify(s.segs)})`);

// (3) Drop, then box around just one segment of W1 (segment 0,
// horizontal) — only that segment is selected, W2 is untouched.
await page.evaluate(() => {
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
});
await dragBox(-20, -20, 130, 20);
s = await page.evaluate(() => ({
  ids: [...state.selectedIds].sort(),
  segs: [...state.selectedSegments].sort(),
}));
assert(JSON.stringify(s.ids) === '["W1"]',
       `single-segment box: W1 is the only id selected`);
assert(JSON.stringify(s.segs) === '["W1|0"]',
       `single-segment box: only W1's segment 0 is highlighted`);

// (4) Render side: when a wire has segment-only selection, the
// underlying polyline does NOT carry the .wire-selected class.
const wireClass = await page.evaluate(() => {
  const path = document.querySelector('path.wire[data-id="W1"]');
  return path ? path.getAttribute('class') : '';
});
assert(wireClass === 'wire',
       `partial selection draws the base polyline plain (got "${wireClass}")`);

// (5) `u` promotes the segment-only selection of W1 to the whole
// net (still just W1 since it's not connected to anything else),
// adding one entry per segment so render still works without a
// special whole-wire branch.
await page.evaluate(() => {
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'u', bubbles: true }));
});
s = await page.evaluate(() => ({
  ids: [...state.selectedIds].sort(),
  segs: [...state.selectedSegments].sort(),
}));
assert(JSON.stringify(s.ids) === '["W1"]',
       `'u' extends segment-only selection to the wire's net`);
assert(JSON.stringify(s.segs) === '["W1|0","W1|1","W1|2","W1|3"]',
       `'u' marks every segment of the promoted wire (got ${JSON.stringify(s.segs)})`);

// (6) Regression: clicking on a wire whose segments came from a
// multi-wire box-select must NOT erase markers on *other* wires.
// Reproduces a user-reported case where after box-selecting a region
// that picked partial segments on two wires, clicking on one wire
// blew away the other wire's per-segment highlights (it reverted to
// a whole-wire highlight). Validates two preservations at once:
//
//   * The clicked wire keeps its multi-segment markers (the click is
//     pure drag-init when the wire has 2+ markers).
//   * Other wires keep their markers regardless.
await page.evaluate(() => {
  state.parts = []; state.wires = []; state.nameCounters = {}; state.nextId = 1;
  state.selectedIds.clear();
  state.selectedSegments.clear();
  state.pan = { x: 80, y: 200 }; state.zoom = 1;
  state.tool = 'select';
  // WA: 6-segment zigzag. The box (below) picks segments 2, 3 and
  // 4 (the lower horizontal/vertical/horizontal sequence), leaving
  // segments 0, 1 and 5 outside.
  state.wires.push({ id: 'WA',
    points: [[0, 0], [60, 0], [60, -40], [120, -40],
             [120, -80], [180, -80], [180, 0]] });
  // WB: a separate wire well clear of WA (≫ HIT_PAD). The box
  // picks segment 0 but not segment 1.
  state.wires.push({ id: 'WB',
    points: [[300, -50], [300, -10], [340, -10]] });
  state.nextId = 3;
  pushHistory(); render();
});
await dragBox(50, -90, 310, -5);
let before = await page.evaluate(() => ({
  ids: [...state.selectedIds].sort(),
  segs: [...state.selectedSegments].sort(),
}));
assert(JSON.stringify(before.ids) === '["WA","WB"]',
       `box covers both wires (got ${JSON.stringify(before.ids)})`);
assert(JSON.stringify(before.segs) === '["WA|2","WA|3","WA|4","WB|0"]',
       `box picks 3 segs of WA + 1 seg of WB (got ${JSON.stringify(before.segs)})`);

async function clickWorld(wx, wy) {
  const wb = await wrapBox();
  const pz = await panZoom();
  await page.mouse.move(wb.left + pz.px + wx * pz.z,
                        wb.top + pz.py + wy * pz.z);
  await page.mouse.down();
  await page.mouse.up();
}
// Click on the midpoint of WA segment 3 (vertical from (120,-40)
// to (120,-80)). Picked unambiguously: WB's nearest point is at
// x=300, well outside HIT_PAD.
await clickWorld(120, -60);
const after = await page.evaluate(() => ({
  ids: [...state.selectedIds].sort(),
  segs: [...state.selectedSegments].sort(),
}));
assert(JSON.stringify(after.ids) === JSON.stringify(before.ids),
       `click on already-selected wire keeps selectedIds intact`);
assert(JSON.stringify(after.segs) === JSON.stringify(before.segs),
       `click does NOT erase markers (got ${JSON.stringify(after.segs)}, ` +
       `expected ${JSON.stringify(before.segs)})`);

await browser.close();
process.exit(summary(errors));
