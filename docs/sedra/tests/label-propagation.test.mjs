// `propagateLabels` semantics:
//   * star — labelling one wire fills siblings sharing a vertex.
//   * join — connecting an unnamed component to a named one inherits.
//   * conflict — two distinct user labels in the same component stay
//     side-by-side and the netlist surfaces a warning.
//   * unname — the prop-pane handler unnames the whole component.
//   * net highlight on a propagated component covers every grid point.
import { setup, assert, assertEqual, summary } from './_helpers.mjs';

const { browser, page, errors } = await setup();

// Star: three wires share one vertex; label one, all three inherit.
const star = await page.evaluate(() => {
  state.parts = []; state.wires = []; state.nameCounters = {}; state.nextId = 1;
  state.selectedIds.clear();
  state.wires.push({ id: 'WA', points: [[0, 0],   [100, 0]] });
  state.wires.push({ id: 'WB', points: [[100, 0], [100, 100]] });
  state.wires.push({ id: 'WC', points: [[100, 0], [200, 0]] });
  state.nextId = 4;
  pushHistory(); render();
  const wb = state.wires.find(w => w.id === 'WB');
  const compIds = wireIdsInSameComponent(wb);
  for (const w of state.wires) if (compIds.has(w.id)) w.label = 'mid';
  pushHistory(); render();
  return state.wires.map(w => w.label);
});
assertEqual(star, ['mid', 'mid', 'mid'], 'star: labelling one wire fills all siblings');

// Join: unnamed component becomes named when connected to a named one.
const join = await page.evaluate(() => {
  state.parts = []; state.wires = []; state.nameCounters = {}; state.nextId = 1;
  state.selectedIds.clear();
  state.wires.push({ id: 'L1', points: [[0, 0],   [60, 0]],   label: 'in' });
  state.wires.push({ id: 'L2', points: [[60, 0],  [60, 60]] });
  state.wires.push({ id: 'R1', points: [[200, 0], [260, 0]] });
  state.wires.push({ id: 'R2', points: [[260, 0], [260, 60]] });
  state.nextId = 5;
  pushHistory(); render();
  state.wires.push({ id: 'C1', points: [[60, 0], [200, 0]] });
  state.nextId = 6;
  pushHistory(); render();
  return state.wires.map(w => ({ id: w.id, label: w.label || null }));
});
for (const w of join) {
  assert(w.label === 'in', `${w.id} ends up labelled "in" after the join`);
}

// Conflict: two distinct user labels in the same component → both
// stay, propagation is a no-op, netlist warns. We use 90° meeting
// points so end-to-end wire merging doesn't fuse the wires before
// the propagation pass even sees them.
const conflict = await page.evaluate(() => {
  state.parts = []; state.wires = []; state.nameCounters = {}; state.nextId = 1;
  state.selectedIds.clear();
  state.wires.push({ id: 'A', points: [[0, 0],   [60, 0]],          label: 'foo' });
  state.wires.push({ id: 'B', points: [[60, 0],  [60, 60], [120, 60]], label: 'bar' });
  state.wires.push({ id: 'C', points: [[120, 60], [120, 120]] });
  state.nextId = 4;
  pushHistory(); render();
  return {
    labels: state.wires.map(w => ({ id: w.id, label: w.label || null })),
    netlist: document.getElementById('netlist').value,
  };
});
const A = conflict.labels.find(w => w.id === 'A');
const B = conflict.labels.find(w => w.id === 'B');
const C = conflict.labels.find(w => w.id === 'C');
assert(A && A.label === 'foo', 'A keeps "foo" through the conflict');
assert(B && B.label === 'bar', 'B keeps "bar" through the conflict');
assert(C && C.label === null, 'C remains unnamed when the component has multiple labels');
assert(/warning: conflicting net labels/.test(conflict.netlist),
       'netlist surfaces a warning about the conflicting labels');

// Unname: clearing one wire's label clears the whole component.
const unname = await page.evaluate(() => {
  state.parts = []; state.wires = []; state.nameCounters = {}; state.nextId = 1;
  state.selectedIds.clear();
  state.wires.push({ id: 'X', points: [[0, 0],   [60, 0]],   label: 'in' });
  state.wires.push({ id: 'Y', points: [[60, 0],  [60, 60]] });
  state.wires.push({ id: 'Z', points: [[60, 60], [120, 60]] });
  state.nextId = 4;
  pushHistory(); render();
  const wy = state.wires.find(w => w.id === 'Y');
  const compIds = wireIdsInSameComponent(wy);
  for (const w of state.wires) if (compIds.has(w.id)) delete w.label;
  pushHistory(); render();
  return state.wires.map(w => w.label || null);
});
assertEqual(unname, [null, null, null], 'unname propagates to the whole component');

// Highlight on a labelled propagated component.
const hl = await page.evaluate(() => {
  state.parts = []; state.wires = []; state.nameCounters = {}; state.nextId = 1;
  state.selectedIds.clear();
  state.pan = { x: 200, y: 200 }; state.zoom = 1;
  state.wires.push({ id: 'A', points: [[0, 0],   [80, 0]],   label: 'mynet' });
  state.wires.push({ id: 'B', points: [[80, 0],  [80, 80]] });
  state.wires.push({ id: 'C', points: [[80, 80], [160, 80]] });
  state.nextId = 4;
  pushHistory(); render();
  setTool('highlight');
  finalizeNetHighlight([0, 0], [0, 0]);
  return {
    node: state.netHighlightOverlay.node,
    pts: [...state.netHighlightOverlay.gridPoints].sort(),
  };
});
assert(hl.node === 'mynet', 'highlight reports the propagated label as the net name');
assertEqual(hl.pts, ['0,0', '160,80', '80,0', '80,80'],
            'highlight covers every grid point in the propagated component');

await browser.close();
process.exit(summary(errors));
