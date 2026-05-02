// Net naming & highlighting: label propagation across components,
// conflict surfacing, highlight reach, cross-component label union.
import { setup, assert, assertEqual, summary } from './_helpers.mjs';

const { browser, page, errors } = await setup();

// (1) Star: labelling one wire fills siblings sharing a vertex.
const star = await page.evaluate(() => {
  state.parts = []; state.wires = []; state.nameCounters = {}; state.nextId = 1;
  state.selectedIds.clear(); state.selectedSegments.clear();
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
assertEqual(star, ['mid', 'mid', 'mid'], 'star: one label fills the component');

// (2) Conflict: two distinct labels in the same component → both stay,
// netlist surfaces a warning. 90° meeting points keep the wires from
// merging end-to-end before propagation runs.
const conflict = await page.evaluate(() => {
  state.parts = []; state.wires = []; state.nameCounters = {}; state.nextId = 1;
  state.selectedIds.clear(); state.selectedSegments.clear();
  state.wires.push({ id: 'A', points: [[0, 0],  [60, 0]],          label: 'foo' });
  state.wires.push({ id: 'B', points: [[60, 0], [60, 60], [120, 60]], label: 'bar' });
  state.wires.push({ id: 'C', points: [[120, 60], [120, 120]] });
  state.nextId = 4;
  pushHistory(); render();
  return {
    labels: state.wires.map(w => ({ id: w.id, label: w.label || null })),
    netlist: document.getElementById('netlist').value,
  };
});
const labelOf = (id) => conflict.labels.find(w => w.id === id)?.label;
assert(labelOf('A') === 'foo' && labelOf('B') === 'bar',
       'conflicting labels both kept verbatim');
assert(labelOf('C') === null, 'unlabelled wire stays unlabelled in conflict');
assert(/warning: conflicting net labels/.test(conflict.netlist),
       'netlist warns about conflicting labels');

// (3) Highlight via shared user label unifies physically separate
// components; clicking empty space clears.
const hl = await page.evaluate(() => {
  state.parts = []; state.wires = []; state.nameCounters = {}; state.nextId = 1;
  state.selectedIds.clear(); state.selectedSegments.clear();
  state.pan = { x: 60, y: 240 }; state.zoom = 1;
  addPart('res', 700, 100, 0);
  addPart('res', 700, 300, 0);
  state.wires.push({ id: 'W1', points: [[660, 100], [600, 100]], label: 'vbias' });
  state.wires.push({ id: 'W2', points: [[660, 300], [600, 300]], label: 'vbias' });
  state.nextId = 3;
  pushHistory(); render();
  setTool('highlight');
  finalizeNetHighlight(snapPt([660, 100]), [660, 100]);
  const lit = { node: state.netHighlightOverlay.node,
                pts: [...state.netHighlightOverlay.gridPoints].sort() };
  // Empty-space click clears.
  finalizeNetHighlight([2000, 2000], [2000, 2000]);
  return { lit, cleared: state.netHighlightOverlay };
});
assert(hl.lit.node === 'vbias', 'highlight reports the user label as net name');
const expected = ['600,100', '600,300', '660,100', '660,300'];
assert(JSON.stringify(hl.lit.pts) === JSON.stringify(expected),
       `vbias highlight unifies both labelled components ` +
       `(got ${JSON.stringify(hl.lit.pts)})`);
assert(hl.cleared === null, 'click on empty space clears the overlay');

await browser.close();
process.exit(summary(errors));
