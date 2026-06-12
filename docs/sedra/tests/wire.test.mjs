// Wire canonicalisation: end-to-end merge, T-junction preservation,
// overlap absorption with label inheritance, dangle-trim on commit,
// and stretch behaviour into congested areas.
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

// (5) Dangle-trim: a stretched wire's path lands on an existing
// stub's track; overlap-merge consumes the stub and the now-orphaned
// endpoint is trimmed on commit.
const trim = await page.evaluate(() => {
  state.parts = []; state.wires = []; state.nameCounters = {}; state.nextId = 1;
  state.selectedIds.clear(); state.selectedSegments.clear();
  state.tool = 'select';
  document.getElementById('drag-mode').checked = true;
  document.getElementById('parity-check').checked = false;
  // R1 with a vertical stub down to (40, 200). Drag R1 down so the
  // stretched stub collapses onto R1's moved terminal and the trunk
  // stretch absorbs the track, leaving no orphan at (40, 0).
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
assert(!hasOrphan, `(40, 0) orphan trimmed after the stretch absorbed it`);

// (6) Stretch into a congested area: under the old router model this
// enclosure was unrouteable and W1 was flagged as a red bad wire.
// The stretch model has no routing step — W1's attached end simply
// follows R1, the wire stays Manhattan, and the drag commits without
// any failure path. (Crossing other parts' terminals is the user's
// call, as in KiCad; the parity checkbox guards it when enabled.)
const congested = await page.evaluate(() => {
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
  const manhattan = (pts) => pts.every((p, i) =>
    i === 0 || p[0] === pts[i-1][0] || p[1] === pts[i-1][1]);
  // R1's left terminal after the move sits at (160, 200); the wire
  // serving it must reach that point with a Manhattan path and no
  // bad flag.
  const serving = state.wires.find(w =>
    w.points.some(p => p[0] === 160 && p[1] === 200));
  return { r1x: r1.x, r1y: r1.y,
           served: !!serving, servedBad: !!serving?.bad,
           servedManhattan: serving ? manhattan(serving.points) : false,
           moveActive: state.moveDraft !== null };
});
assert(congested.r1x === 200 && congested.r1y === 200,
       `drag into congestion commits (got (${congested.r1x}, ${congested.r1y}))`);
assert(congested.served && !congested.servedBad && congested.servedManhattan,
       'attached wire follows as a clean Manhattan path — no bad-wire fallback');
assert(!congested.moveActive, 'no stuck move-draft');

await browser.close();
process.exit(summary(errors));
