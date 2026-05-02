// Drag end-to-end smoke: classification, BFS commit, parity revert,
// and the partial-segment fixture drag.
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

// (1) Classification + BFS Manhattan commit.
await buildChain();
const drag = await page.evaluate(() => {
  document.getElementById('drag-mode').checked = true;
  document.getElementById('parity-check').checked = true;
  state.selectedIds = new Set(['R1', 'R2']);
  startMove(['R1', 'R2'], [0, 0], true, false);
  const kinds = Object.fromEntries(
    [...state.moveDraft.origs.entries()].map(([id, o]) => [id, o.kind]));
  updateMove([0, 80]);
  commitMove();
  const wires = Object.fromEntries(state.wires.map(w => [w.id, w.points]));
  const manhattan = (pts) => pts.every((p, i) =>
    i === 0 || p[0] === pts[i-1][0] || p[1] === pts[i-1][1]);
  return {
    kinds,
    R1: state.parts.find(p => p.id === 'R1'),
    W2first: wires.W2[0], W2last: wires.W2[wires.W2.length - 1],
    W1manhattan: manhattan(wires.W1), W3manhattan: manhattan(wires.W3),
  };
});
assert(drag.kinds.R1 === 'part' && drag.kinds.W2 === 'wire-captured'
       && drag.kinds.W1 === 'wire-spanning' && drag.kinds.W3 === 'wire-spanning',
       'classifies parts / captured (both ends on selection) / spanning (one end)');
assert(drag.R1.x === 200 && drag.R1.y === 80, 'R1 moved to (200, 80)');
assert(drag.W2first[0] === 240 && drag.W2first[1] === 80
       && drag.W2last[0] === 360 && drag.W2last[1] === 80,
       'captured W2 translates with the moved cluster');
assert(drag.W1manhattan && drag.W3manhattan,
       'BFS commit lays Manhattan paths for spanning wires');

// (2) Parity revert: dragging R1 to overlap R2's terminals reverts.
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
  commitMove();
  return {
    parityHeld: netSignature() === sigBefore,
    R1: state.parts.find(p => p.id === 'R1'),
  };
});
assert(revert.parityHeld, 'parity revert restores connectivity signature');
assert(revert.R1.x === 200 && revert.R1.y === 0, 'R1 restored to pre-drag position');

// (3) Fixture box-drag: partial-segment selection drags only the
// selected segments and parity catches the mid-segment T short.
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
  const ids = [...state.selectedIds];
  startMove(ids, [400, 320], true, false);
  updateMove([320, 660]);
  commitMove();
  return { sigAfter: netSignature(),
           parts: state.parts.map(p => ({ id: p.id, x: p.x, y: p.y })),
           moveActive: state.moveDraft !== null };
});
assert(!after.moveActive, 'fixture drag finishes (no stuck moveDraft)');
assert(after.sigAfter === fix.sigBefore,
       'fixture drag reverted because BFS routes would short via mid-segment Ts');
const drift = fix.parts.find(p => {
  const q = after.parts.find(x => x.id === p.id);
  return !q || q.x !== p.x || q.y !== p.y;
});
assert(!drift, `every fixture part restored to its pre-drag position` +
       (drift ? ` (drift: ${drift.id})` : ''));

await browser.close();
process.exit(summary(errors));
