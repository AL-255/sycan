// Drag end-to-end smoke: KiCad-style attachment classification
// (captured / stretch / stub), by-construction connectivity, parity
// revert on real shorts, and the partial-segment fixture drag.
import { setup, assert, summary } from './_helpers.mjs';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const fixture = readFileSync(join(here, 'fixtures', 'circuit-1.json'), 'utf8');

const { browser, page, errors } = await setup();

const buildChain = () => page.evaluate(() => {
  state.parts = []; state.wires = []; state.nameCounters = {}; state.nextId = 1;
  state.selectedIds.clear(); state.selectedSegments.clear();
  state.pan = { x: 80, y: 200 }; state.zoom = 1; state.tool = 'select';
  addPart('vsrc', 0,   0, 90);
  addPart('res',  200, 0, 90);
  addPart('res',  400, 0, 90);
  addPart('cap',  600, 0, 90);
  state.wires.push({ id: 'W1', points: [[40,  0], [160, 0]] });
  state.wires.push({ id: 'W2', points: [[240, 0], [360, 0]] });
  state.wires.push({ id: 'W3', points: [[440, 0], [560, 0]] });
  state.nextId = 4;
  pushHistory(); render();
});

// (1) Classification + live-Manhattan stretch commit.
await buildChain();
const drag = await page.evaluate(() => {
  document.getElementById('drag-mode').checked = true;
  document.getElementById('parity-check').checked = true;
  const sigBefore = netSignature();
  state.selectedIds = new Set(['R1', 'R2']);
  startMove(['R1', 'R2'], [0, 0], true, false);
  const kinds = Object.fromEntries(
    [...state.moveDraft.origs.entries()].map(([id, o]) => [id, o.kind]));
  updateMove([0, 80]);
  // Mid-drag, every wire must already be Manhattan (the stretch
  // inserts bends live; there is no diagonal placeholder phase).
  const manhattan = (pts) => pts.every((p, i) =>
    i === 0 || p[0] === pts[i-1][0] || p[1] === pts[i-1][1]);
  const liveManhattan = state.wires.every(w => manhattan(w.points));
  commitMove();
  const wires = Object.fromEntries(state.wires.map(w => [w.id, w.points]));
  return {
    kinds, liveManhattan,
    sigHeld: netSignature() === sigBefore,
    R1: state.parts.find(p => p.id === 'R1'),
    W2first: wires.W2[0], W2last: wires.W2[wires.W2.length - 1],
    W1manhattan: manhattan(wires.W1), W3manhattan: manhattan(wires.W3),
  };
});
assert(drag.kinds.R1 === 'part' && drag.kinds.W2 === 'wire-captured'
       && drag.kinds.W1 === 'wire-stretch' && drag.kinds.W3 === 'wire-stretch',
       'classifies parts / captured (both ends on selection) / stretch (one end)');
assert(drag.R1.x === 200 && drag.R1.y === 80, 'R1 moved to (200, 80)');
assert(drag.W2first[0] === 240 && drag.W2first[1] === 80
       && drag.W2last[0] === 360 && drag.W2last[1] === 80,
       'captured W2 translates with the moved cluster');
assert(drag.liveManhattan, 'wires stay Manhattan during the drag itself');
assert(drag.W1manhattan && drag.W3manhattan,
       'stretched wires commit as Manhattan paths');
assert(drag.sigHeld, 'connectivity preserved by construction');

// (2) Parity revert: dragging R1 to overlap R2's terminals creates a
// genuine short (the only thing the parity check can catch now that
// stretch preserves existing connections by construction) → revert.
// There is no retry loop any more, so commit must be fast; 600 ms
// cap absorbs startup jitter and the canonicalize/render passes.
const revert = await page.evaluate(() => {
  state.parts = []; state.wires = []; state.nameCounters = {}; state.nextId = 1;
  state.selectedIds.clear(); state.selectedSegments.clear();
  addPart('vsrc', 0,   0,  90);
  addPart('res',  200, 0,  90);
  addPart('res',  200, 80, 90);
  state.wires.push({ id: 'W1', points: [[40, 0], [160, 0]] });
  state.nextId = 2;
  pushHistory(); render();
  document.getElementById('drag-mode').checked = true;
  document.getElementById('parity-check').checked = true;
  const sigBefore = netSignature();
  state.selectedIds = new Set(['R1']);
  startMove(['R1'], [0, 0], true, false);
  updateMove([0, 80]);
  const t0 = performance.now();
  commitMove();
  const elapsed = performance.now() - t0;
  return {
    parityHeld: netSignature() === sigBefore,
    R1: state.parts.find(p => p.id === 'R1'),
    elapsed,
  };
});
assert(revert.parityHeld, 'parity revert restores connectivity signature');
assert(revert.R1.x === 200 && revert.R1.y === 0, 'R1 restored to pre-drag position');
assert(revert.elapsed < 600,
       `shorting drag reverts immediately — commitMove took ` +
       `${revert.elapsed.toFixed(1)} ms (cap 600 ms, no retry loop)`);

// (3) Fixture box-drag: partial-segment selection drags only the
// selected segments. Two deltas probe the stretch model's two
// outcomes:
//
//   * Dragging the cluster 340 down *along its own column* slides
//     the stretched wires collinearly over unselected remainder
//     pieces of other nets. SEDRA merges overlapping collinear
//     wires into one net (unlike KiCad, where overlap alone never
//     connects), so this drag is a genuine short — the parity
//     guard must revert it with everything restored.
//   * Dragging the same cluster sideways out of the corridor is
//     clean: stretch preserves every connection by construction and
//     the drag commits with parity intact.
const fix = await page.evaluate((jsonText) => {
  const data = JSON.parse(jsonText);
  state.parts = data.parts || [];
  state.wires = data.wires || [];
  state.nameCounters = data.nameCounters || {};
  state.nextId = data.parts.length + data.wires.length + 1;
  state.selectedIds.clear(); state.selectedSegments.clear();
  state.pan = { x: 80, y: 80 }; state.zoom = 1; state.tool = 'select';
  document.getElementById('drag-mode').checked = true;
  document.getElementById('parity-check').checked = true;
  pushHistory(); render();
  return { sigBefore: netSignature(),
           parts: state.parts.map(p => ({ id: p.id, x: p.x, y: p.y })) };
}, fixture);

const wb = await page.evaluate(() => {
  const r = document.getElementById('canvas-wrap').getBoundingClientRect();
  return { left: r.left, top: r.top, px: state.pan.x, py: state.pan.y };
});
const sx = (x) => wb.left + wb.px + x;
const sy = (y) => wb.top + wb.py + y;
await page.mouse.move(sx(500), sy(420));
await page.mouse.down();
await page.mouse.move(sx(380), sy(220), { steps: 6 });
await page.mouse.up();

const after = await page.evaluate(() => {
  // (a) Down-the-column drag: collinear overlap with other nets'
  // remainder pieces → genuine short → parity revert.
  const ids = [...state.selectedIds];
  const partsBefore = JSON.stringify(
    state.parts.map(p => ({ id: p.id, x: p.x, y: p.y })));
  const wiresBefore = JSON.stringify(state.wires.map(w => w.points));
  startMove(ids, [400, 320], true, false);
  updateMove([320, 660]);
  commitMove();
  const reverted = {
    sigAfter: netSignature(),
    partsRestored: JSON.stringify(
      state.parts.map(p => ({ id: p.id, x: p.x, y: p.y }))) === partsBefore,
    wiresRestored: JSON.stringify(state.wires.map(w => w.points)) === wiresBefore,
    moveActive: state.moveDraft !== null,
  };

  // (b) Sideways drag out of the corridor: no overlap, commits.
  for (const id of ids) {
    if (state.parts.some(p => p.id === id)) state.selectedIds.add(id);
  }
  startMove(ids, [400, 320], true, false);
  updateMove([700, 320]);
  commitMove();
  return {
    reverted,
    sigAfter: netSignature(),
    parts: state.parts.map(p => ({ id: p.id, x: p.x, y: p.y })),
    moveActive: state.moveDraft !== null,
  };
});
assert(!after.reverted.moveActive && !after.moveActive,
       'fixture drags finish (no stuck moveDraft)');
assert(after.reverted.sigAfter === fix.sigBefore
       && after.reverted.partsRestored && after.reverted.wiresRestored,
       'down-the-column drag would short (SEDRA merges overlapping ' +
       'collinear wires) — parity guard reverts it completely');
assert(after.sigAfter === fix.sigBefore,
       'sideways drag preserves connectivity by construction');
// Delta (+300, 0) from pickup (400, 320) → release (700, 320).
const r2 = after.parts.find(p => p.id === 'R2');
assert(r2 && r2.x === 700 && r2.y === 320,
       `R2 committed at the dragged position (got (${r2?.x}, ${r2?.y}))`);
const r1 = after.parts.find(p => p.id === 'R1');
assert(r1 && r1.x === 320 && r1.y === 320,
       `R1 (outside the box-select) stayed put (got (${r1?.x}, ${r1?.y}))`);

// (4) T-junction attachment: R2's terminal sits on an interior
// vertex of W1 (a Steiner T). Dragging R2 away must spawn a stretch
// stub that keeps it connected — the classic "open-ended stub /
// silently broken net" bug in the old model.
const tee = await page.evaluate(() => {
  state.parts = []; state.wires = []; state.nameCounters = {}; state.nextId = 1;
  state.selectedIds.clear(); state.selectedSegments.clear();
  state.tool = 'select';
  document.getElementById('drag-mode').checked = true;
  document.getElementById('parity-check').checked = true;
  addPart('vsrc', -200, 0, 90);                    // V1: (-240,0),(-160,0)
  addPart('res',  200, 0, 90);                     // R1: (160,0),(240,0)
  addPart('res',  0, -200, 0);                     // R2: (0,-240),(0,-160)
  // Trunk with an explicit T vertex at (0,0); R2's lower terminal is
  // wired down to the T by a short stub the canonical form keeps.
  state.wires.push({ id: 'W1', points: [[-160, 0], [0, 0], [160, 0]] });
  state.wires.push({ id: 'W2', points: [[0, -160], [0, 0]] });
  state.nextId = 3;
  pushHistory(); render();
  const sigBefore = netSignature();
  state.selectedIds = new Set(['R2']);
  startMove(['R2'], [0, -200], true, false);
  updateMove([120, -280]);
  commitMove();
  const w1 = state.wires.find(w => w.id === 'W1');
  return {
    sigHeld: netSignature() === sigBefore,
    moveActive: state.moveDraft !== null,
    trunkIntact: !!w1 && w1.points.some(p => p[0] === -160 && p[1] === 0)
                      && w1.points.some(p => p[0] === 160 && p[1] === 0),
  };
});
assert(!tee.moveActive, 'T-drag finishes cleanly');
assert(tee.sigHeld, 'dragging a part off a T keeps it connected (stub spawned)');
assert(tee.trunkIntact, 'the fixed trunk wire stays anchored');

// (5) Terminal-on-terminal attachment: R1 and R2 terminals coincide
// with no wire between them. Dragging R2 away must spawn a bridging
// wire (KiCad's makeNewWire for fixed pins) instead of silently
// breaking the connection.
const pin = await page.evaluate(() => {
  state.parts = []; state.wires = []; state.nameCounters = {}; state.nextId = 1;
  state.selectedIds.clear(); state.selectedSegments.clear();
  state.tool = 'select';
  document.getElementById('drag-mode').checked = true;
  document.getElementById('parity-check').checked = true;
  addPart('res', 0, 0, 90);      // R1: (-40,0),(40,0)
  addPart('res', 120, 0, 90);    // R2: (80,0),(160,0)... move to touch below
  const r2 = state.parts.find(p => p.id === 'R2');
  r2.x = 80; r2.y = 0;           // R2 terminals: (40,0),(120,0) — touches R1
  pushHistory(); render();
  const sigBefore = netSignature();
  state.selectedIds = new Set(['R2']);
  startMove(['R2'], [80, 0], true, false);
  updateMove([80, 160]);
  commitMove();
  return {
    sigHeld: netSignature() === sigBefore,
    wireCount: state.wires.length,
    moveActive: state.moveDraft !== null,
  };
});
assert(!pin.moveActive, 'terminal-stub drag finishes cleanly');
assert(pin.sigHeld,
       'dragging a part off a coincident terminal spawns a bridging wire');
assert(pin.wireCount >= 1, 'the bridge exists as a real wire');

// (6) Drag away and back: no leftover stubs or extra wires — the
// zero-length cleanup removes everything the round trip created.
const roundTrip = await page.evaluate(() => {
  state.parts = []; state.wires = []; state.nameCounters = {}; state.nextId = 1;
  state.selectedIds.clear(); state.selectedSegments.clear();
  state.tool = 'select';
  document.getElementById('drag-mode').checked = true;
  document.getElementById('parity-check').checked = true;
  addPart('vsrc', 0, 0, 90);
  addPart('res', 200, 0, 90);
  state.wires.push({ id: 'W1', points: [[40, 0], [160, 0]] });
  state.nextId = 2;
  pushHistory(); render();
  const before = JSON.stringify(state.wires.map(w => w.points));
  state.selectedIds = new Set(['R1']);
  startMove(['R1'], [200, 0], true, false);
  updateMove([200, 120]);
  updateMove([200, 0]);          // back to the origin
  commitMove();
  return {
    same: JSON.stringify(state.wires.map(w => w.points)) === before,
    wireCount: state.wires.length,
  };
});
assert(roundTrip.same && roundTrip.wireCount === 1,
       `drag-away-and-back leaves the schematic untouched ` +
       `(${roundTrip.wireCount} wires)`);

await browser.close();
process.exit(summary(errors));
