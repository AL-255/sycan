// Drag-mode behaviour:
//   * `wire-captured`  — a wire whose endpoints both touch selected
//                        parts translates with the group.
//   * `wire-spanning`  — a wire with one endpoint on a selected part
//                        gets re-routed each frame (KiCad-style "drag
//                        the end" — see drag-end.test.mjs for the
//                        end-only re-route invariants).
//   * drag-mode OFF    — only the explicit selection moves.
//   * parity ON        — a drag that would change connectivity is
//                        reverted on commit.
import { setup, assert, summary, fmtWire } from './_helpers.mjs';

const { browser, page, errors } = await setup();

// V1 — R1 — R2 — C1 chain, horizontal layout.
const buildChain = () => page.evaluate(() => {
  state.parts = []; state.wires = []; state.nameCounters = {}; state.nextId = 1;
  state.selectedIds.clear();
  state.pan = { x: 80, y: 200 }; state.zoom = 1;
  addPart('vsrc', 0,   0, 90);
  addPart('res',  200, 0, 90);
  addPart('res',  400, 0, 90);
  addPart('cap',  600, 0, 90);
  state.wires.push({ id: 'W1', points: [[40,  0], [160, 0]] });   // V1 → R1
  state.wires.push({ id: 'W2', points: [[240, 0], [360, 0]] });   // R1 → R2
  state.wires.push({ id: 'W3', points: [[440, 0], [560, 0]] });   // R2 → C1
  state.nextId = 4;
  pushHistory(); render();
});

await buildChain();

// (1) drag mode ON: classify origs, drag, inspect result.
const dragRes = await page.evaluate(() => {
  document.getElementById('drag-mode').checked = true;
  document.getElementById('parity-check').checked = true;

  state.tool = 'select';
  state.selectedIds = new Set(['R1', 'R2']);
  startMove(['R1', 'R2'], [0, 0], /*viaDrag=*/true, /*freshlyPasted=*/false);
  const origKinds = Object.fromEntries(
    [...state.moveDraft.origs.entries()].map(([id, o]) => [id, o.kind]));
  updateMove([0, 80]);
  commitMove();
  return {
    origKinds,
    parts: Object.fromEntries(state.parts.map(p => [p.id, [p.x, p.y]])),
    wires: Object.fromEntries(state.wires.map(w => [w.id, w.points])),
  };
});
assert(dragRes.origKinds.R1 === 'part' && dragRes.origKinds.R2 === 'part',
       'selected parts classified as `part`');
assert(dragRes.origKinds.W2 === 'wire-captured',
       'W2 (between two selected parts) is captured');
assert(dragRes.origKinds.W1 === 'wire-spanning',
       'W1 (one end on R1) is spanning');
assert(dragRes.origKinds.W3 === 'wire-spanning',
       'W3 (one end on R2) is spanning');

assert(JSON.stringify(dragRes.parts.R1) === '[200,80]',
       'R1 moved to (200, 80)');
assert(JSON.stringify(dragRes.parts.R2) === '[400,80]',
       'R2 moved to (400, 80)');
// W2 (captured) translated by (0, 80) so endpoints land on the moved
// terminals; the seam coincides with R1.right and R2.left after the
// move, which the post-commit merge/simplify pass might collapse, but
// the start/end must still match.
assert(dragRes.wires.W2[0][0] === 240 && dragRes.wires.W2[0][1] === 80,
       'W2 first vertex now at R1.right (240, 80)');
const W2last = dragRes.wires.W2[dragRes.wires.W2.length - 1];
assert(W2last[0] === 360 && W2last[1] === 80,
       'W2 last vertex now at R2.left (360, 80)');

// (2) drag mode OFF: only selected parts move.
await buildChain();
const noDrag = await page.evaluate(() => {
  document.getElementById('drag-mode').checked = false;
  document.getElementById('parity-check').checked = false;
  state.tool = 'select';
  state.selectedIds = new Set(['R1', 'R2']);
  startMove(['R1', 'R2'], [0, 0], /*viaDrag=*/true, /*freshlyPasted=*/false);
  updateMove([0, 80]);
  commitMove();
  return {
    parts: Object.fromEntries(state.parts.map(p => [p.id, [p.x, p.y]])),
    wires: Object.fromEntries(state.wires.map(w => [w.id, w.points])),
  };
});
assert(JSON.stringify(noDrag.parts.R1) === '[200,80]', 'drag-off: R1 still moves');
assert(JSON.stringify(noDrag.wires.W1) === '[[40,0],[160,0]]',
       'drag-off: W1 stays put (now disconnected from moved R1)');
assert(JSON.stringify(noDrag.wires.W2) === '[[240,0],[360,0]]',
       'drag-off: W2 stays put');

// (3) Parity revert. Set up two parts whose terminals will collide
// after the drag — pre-drag they're in different nets, post-drag
// they'd merge. Parity check kicks in on commit and restores the
// originals.
const revert = await page.evaluate(() => {
  state.parts = []; state.wires = []; state.nameCounters = {}; state.nextId = 1;
  state.selectedIds.clear();
  state.pan = { x: 80, y: 200 }; state.zoom = 1;

  // R1 at (200, 0); after dragging by (0, 80) its terminals will
  // coincide with R2's terminals (R2 sits exactly where R1 lands).
  addPart('vsrc', 0,   0,  90);
  addPart('res',  200, 0,  90);    // R1: terminals (160, 0), (240, 0)
  addPart('res',  200, 80, 90);    // R2: terminals (160, 80), (240, 80)
  state.wires.push({ id: 'W1', points: [[40, 0], [160, 0]] });
  state.nextId = 2;
  pushHistory(); render();

  document.getElementById('drag-mode').checked = true;
  document.getElementById('parity-check').checked = true;

  const sigBefore = netSignature();

  state.tool = 'select';
  state.selectedIds = new Set(['R1']);
  startMove(['R1'], [0, 0], /*viaDrag=*/true, /*freshlyPasted=*/false);
  updateMove([0, 80]);
  commitMove();

  return {
    parityHeld: netSignature() === sigBefore,
    R1: state.parts.find(p => p.id === 'R1'),
    W1: state.wires.find(w => w.id === 'W1').points,
  };
});
assert(revert.parityHeld, 'net-signature unchanged from pre-drag (parity revert restored everything)');
assert(revert.R1.x === 200 && revert.R1.y === 0,
       `R1 restored to original position (got ${revert.R1.x}, ${revert.R1.y})`);
assert(JSON.stringify(revert.W1) === '[[40,0],[160,0]]',
       'W1 restored to original points');

await browser.close();
process.exit(summary(errors));
