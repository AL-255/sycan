// Net Highlight tool:
//   * physical reach via the connected component's grid points.
//   * cross-component name propagation (two physically separate
//     wires labelled "vbias" both light up).
//   * clicking empty space clears the overlay.
import { setup, assert, summary } from './_helpers.mjs';

const { browser, page, errors } = await setup();

await page.evaluate(() => {
  state.parts = []; state.wires = []; state.nameCounters = {}; state.nextId = 1;
  state.selectedIds.clear();
  state.pan = { x: 60, y: 240 }; state.zoom = 1;

  // Main loop: V1 — R1 — C1 — GND
  addPart('vsrc', 0,    300, 0);
  addPart('res',  140,  300, 0);
  addPart('cap',  280,  300, 0);
  addPart('gnd',  420,  340, 0);

  state.wires.push({ id: 'W1', points: [[40, 300], [100, 300]] });
  state.wires.push({ id: 'W2', points: [[180, 300], [240, 300]] });
  state.wires.push({ id: 'W3', points: [[320, 300], [420, 300], [420, 320]] });
  state.wires.push({ id: 'W4', points: [[-40, 300], [-40, 400], [420, 400]] });

  // Two physically separate "vbias" components — each on the left
  // side of its own resistor.
  addPart('res', 700, 100, 0);
  addPart('res', 700, 300, 0);
  state.wires.push({ id: 'W5', points: [[660, 100], [600, 100]], label: 'vbias' });
  state.wires.push({ id: 'W6', points: [[740, 100], [800, 100]] });
  state.wires.push({ id: 'W7', points: [[660, 300], [600, 300]], label: 'vbias' });
  state.wires.push({ id: 'W8', points: [[740, 300], [800, 300]] });
  state.nextId = 9;
  pushHistory(); render();
});

// Clicking W3 highlights its own connected component only (auto-numbered net).
const hlW3 = await page.evaluate(() => {
  setTool('highlight');
  finalizeNetHighlight(snapPt([320, 300]), [320, 300]);
  return {
    node: state.netHighlightOverlay.node,
    n: state.netHighlightOverlay.gridPoints.size,
  };
});
assert(hlW3.n === 3,
       `auto-numbered W3 highlight covers exactly W3's vertices (got ${hlW3.n})`);

// Clicking W5 (label "vbias") also highlights W7's component
// because they share the same user label.
const hlVbias = await page.evaluate(() => {
  setTool('highlight');
  finalizeNetHighlight(snapPt([660, 100]), [660, 100]);
  return {
    node: state.netHighlightOverlay.node,
    pts: [...state.netHighlightOverlay.gridPoints].sort(),
  };
});
assert(hlVbias.node === 'vbias', 'highlight reports the user label as the net name');
const expected = new Set(['600,100', '660,100', '600,300', '660,300']);
const actual = new Set(hlVbias.pts);
const covers = ['600,100', '660,100', '600,300', '660,300']
  .every(k => actual.has(k));
const noBleed = !actual.has('40,300') && !actual.has('180,300')
             && !actual.has('740,100') && !actual.has('800,300');
assert(covers,  '"vbias" highlight covers both labelled components');
assert(noBleed, '"vbias" highlight stays inside the labelled components');
assert(hlVbias.pts.length === expected.size,
       `total grid-point count is ${expected.size} (got ${hlVbias.pts.length})`);

// Clicking empty space clears the overlay.
const clear = await page.evaluate(() => {
  setTool('highlight');
  finalizeNetHighlight([2000, 2000], [2000, 2000]);
  return state.netHighlightOverlay;
});
assert(clear === null, 'clicking empty space clears the overlay');

await browser.close();
process.exit(summary(errors));
