// Wire canonicalisation: end-to-end merge, T-junction preservation,
// overlap absorption with label inheritance, dangle-trim on commit,
// and bad-wire emission when BFS fails.
import { setup, assert, assertEqual, summary } from './_helpers.mjs';

const { browser, page, errors } = await setup();

const seed = (parts, wires) => page.evaluate(({ parts, wires }) => {
  state.parts = []; state.wires = []; state.nameCounters = {}; state.nextId = 1;
  state.selectedIds.clear(); state.selectedSegments.clear();
  for (const p of parts) addPart(p.type, p.x, p.y, p.rot || 0);
  for (const w of wires) state.wires.push({ ...w });
  state.nextId = wires.length + 1;
  pushHistory(); render();
  return state.wires.map(w => ({ id: w.id, points: w.points, label: w.label }));
}, { parts, wires });

// (1) Collinear pair with free seam fuses end-to-end.
let r = await seed([], [
  { id: 'A', points: [[0, 0], [40, 0]] },
  { id: 'B', points: [[40, 0], [80, 0]] },
]);
assertEqual(r, [{ id: 'A', points: [[0, 0], [80, 0]], label: undefined }],
            'collinear pair with free seam fuses');

// (2) T at the seam preserves all three.
r = await seed([], [
  { id: 'A', points: [[0, 0],  [40, 0]] },
  { id: 'B', points: [[40, 0], [80, 0]] },
  { id: 'C', points: [[40, 0], [40, 40]] },
]);
assertEqual(r.length, 3, 'T-junction at seam blocks merge');

// (3) 90° corner inside one wire is preserved.
r = await seed([], [
  { id: 'A', points: [[0, 0], [40, 0], [40, 40]] },
]);
assertEqual(r[0].points, [[0, 0], [40, 0], [40, 40]], '90° corner survives');

// (4) Overlap-merge: lower-indexed wire absorbs the duplicate and
// inherits the absorbed wire's label.
r = await seed([], [
  { id: 'W1', points: [[0, 0], [400, 0]] },
  { id: 'W2', points: [[200, 0], [600, 0]], label: 'BUS' },
]);
assertEqual(r.length, 1, 'overlapping collinears collapse to one');
assertEqual(r[0].id, 'W1', 'lower-indexed wire wins ownership');
assertEqual(r[0].points, [[0, 0], [600, 0]], 'union interval [0,600]');
assertEqual(r[0].label, 'BUS', 'unlabelled primary inherits absorbed label');

// (5) Dangle-trim: spanning wire BFS overlays an existing stub; the
// stub's now-orphaned endpoint is trimmed on commit.
const trim = await page.evaluate(() => {
  state.parts = []; state.wires = []; state.nameCounters = {}; state.nextId = 1;
  state.selectedIds.clear(); state.selectedSegments.clear();
  state.tool = 'select';
  document.getElementById('drag-mode').checked = true;
  document.getElementById('parity-check').checked = false;
  // R1 with a vertical stub down to (40, 200). Drag R1 down so BFS
  // routes through the stub's track and overlap-merge consumes it,
  // leaving (40, 200) as a pre-drag-free orphan that should be trimmed.
  addPart('res', 0, 0, 90);                              // R1 terminals (-40,0),(40,0)
  addPart('vsrc', -200, 0, 90);                          // V1 anchors W1 outside endpoint
  state.wires.push({ id: 'W1', points: [[-160, 0], [-40, 0]] });
  state.wires.push({ id: 'W2', points: [[40, 0], [40, 200]] });
  state.nextId = 3;
  pushHistory(); render();
  state.selectedIds = new Set(['R1']);
  startMove(['R1'], [0, 0], true, false);
  updateMove([0, 200]);
  commitMove();
  return state.wires.map(w => ({ id: w.id, points: w.points }));
});
const hasOrphan = trim.some(w => w.points.some(p => p[0] === 40 && p[1] === 0));
assert(!hasOrphan, `(40, 0) orphan trimmed after spanning route absorbed it`);

// (6) BFS-fail emits a bad wire and the drag still commits.
const bad = await page.evaluate(() => {
  state.parts = []; state.wires = []; state.nameCounters = {}; state.nextId = 1;
  state.selectedIds.clear(); state.selectedSegments.clear();
  state.tool = 'select';
  document.getElementById('drag-mode').checked = true;
  document.getElementById('parity-check').checked = false;
  // Enclose grid cell (160, 200) on all four sides.
  addPart('vsrc', 160, 140, 0);
  addPart('vsrc', 160, 260, 0);
  addPart('vsrc', 100, 200, 90);
  addPart('vsrc', 220, 200, 90);
  addPart('res', 600, 200, 90);
  state.wires.push({ id: 'W1', points: [[560, 200], [800, 200]] });
  state.nextId = 2;
  pushHistory(); render();
  state.selectedIds = new Set(['R1']);
  startMove(['R1'], [600, 200], true, false);
  updateMove([200, 200]);   // R1.left lands inside the enclosure
  commitMove();
  const r1 = state.parts.find(p => p.id === 'R1');
  const w1 = state.wires.find(w => w.id === 'W1');
  return { r1x: r1.x, r1y: r1.y, w1Bad: !!w1?.bad,
           hasBadClass: !!document.querySelector('.wire-bad[data-id="W1"]') };
});
assert(bad.r1x === 200 && bad.r1y === 200,
       `R1 committed at the dragged position despite BFS failure (got (${bad.r1x}, ${bad.r1y}))`);
assert(bad.w1Bad, 'failed-route wire flagged bad=true');
assert(bad.hasBadClass, 'bad wire renders with .wire-bad class');

await browser.close();
process.exit(summary(errors));
