// Drag-mode "end-only" reroute + undo dedupe behaviour.
//   * end-only reroute keeps every original middle vertex; only the
//     last segment is stretched / cornered.
//   * along-axis drag inserts no new corner.
//   * undo round-trip restores the pre-drag state exactly.
//   * zero-delta moves don't pad history (`pushHistory` dedupe).
import { setup, assertEqual, assert, summary, fmtWire } from './_helpers.mjs';

const { browser, page, errors } = await setup();

// (A) Z-shaped wire between V1 and R1; drag R1 → middle stays.
const a = await page.evaluate(() => {
  state.parts = []; state.wires = []; state.nameCounters = {}; state.nextId = 1;
  state.selectedIds.clear();
  addPart('vsrc', 0,   0, 90);
  addPart('res',  600, 0, 90);
  state.wires.push({ id: 'W1', points: [
    [40,  0], [200,  0], [200, -100], [400, -100], [400, 0], [560, 0],
  ]});
  state.nextId = 2;
  pushHistory(); render();

  document.getElementById('drag-mode').checked = true;
  document.getElementById('parity-check').checked = false;

  state.tool = 'select';
  state.selectedIds = new Set(['R1']);
  startMove(['R1'], [0, 0], /*viaDrag=*/true, /*freshlyPasted=*/false);
  updateMove([0, 80]);
  commitMove();
  return state.wires.find(w => w.id === 'W1').points;
});
assertEqual(a,
  [[40, 0], [200, 0], [200, -100], [400, -100], [400, 0], [560, 0], [560, 80]],
  'Z-shaped W1 keeps every original middle vertex; an L is appended at the dragged end');

// (B) along-axis drag: no new corner inserted.
const b = await page.evaluate(() => {
  state.parts = []; state.wires = []; state.nameCounters = {}; state.nextId = 1;
  state.selectedIds.clear();
  addPart('vsrc', 0,   0, 90);
  addPart('res',  200, 0, 90);
  state.wires.push({ id: 'W1', points: [[40, 0], [160, 0]] });
  state.nextId = 2;
  pushHistory(); render();
  document.getElementById('drag-mode').checked = true;
  document.getElementById('parity-check').checked = false;
  state.tool = 'select';
  state.selectedIds = new Set(['R1']);
  startMove(['R1'], [0, 0], /*viaDrag=*/true, /*freshlyPasted=*/false);
  updateMove([80, 0]);   // drag along the wire's own axis
  commitMove();
  return state.wires.find(w => w.id === 'W1').points;
});
assertEqual(b, [[40, 0], [240, 0]],
            'along-axis drag stretches the last segment with no new corner');

// (C) undo round-trip restores pre-drag state.
const c = await page.evaluate(() => {
  state.parts = []; state.wires = []; state.nameCounters = {}; state.nextId = 1;
  state.selectedIds.clear();
  addPart('vsrc', 0,   0, 90);
  addPart('res',  200, 0, 90);
  addPart('res',  400, 0, 90);
  addPart('cap',  600, 0, 90);
  state.wires.push({ id: 'W1', points: [[40,  0], [160, 0]] });
  state.wires.push({ id: 'W2', points: [[240, 0], [360, 0]] });
  state.wires.push({ id: 'W3', points: [[440, 0], [560, 0]] });
  state.nextId = 4;
  pushHistory(); render();
  const pre = JSON.stringify({
    parts: state.parts,
    wires: state.wires.map(w => ({ id: w.id, points: w.points })),
  });
  document.getElementById('drag-mode').checked = true;
  document.getElementById('parity-check').checked = true;
  state.tool = 'select';
  state.selectedIds = new Set(['R1', 'R2']);
  startMove(['R1', 'R2'], [0, 0], /*viaDrag=*/true, /*freshlyPasted=*/false);
  updateMove([0, 80]);
  commitMove();
  const post = JSON.stringify({
    parts: state.parts,
    wires: state.wires.map(w => ({ id: w.id, points: w.points })),
  });
  document.getElementById('btn-undo').click();
  const undone = JSON.stringify({
    parts: state.parts,
    wires: state.wires.map(w => ({ id: w.id, points: w.points })),
  });
  return { changed: pre !== post, restored: pre === undone };
});
assert(c.changed, 'drag actually changed state');
assert(c.restored, 'one Ctrl+Z restores the pre-drag state exactly');

// (D) zero-delta no-op moves don't pad history.
const d = await page.evaluate(() => {
  state.parts = []; state.wires = []; state.nameCounters = {}; state.nextId = 1;
  state.selectedIds.clear();
  addPart('res', 0, 0, 0);
  state.nextId = 2;
  pushHistory(); render();
  const before = editHistory.length;
  state.tool = 'select';
  state.selectedIds = new Set(['R1']);
  for (let i = 0; i < 3; i++) {
    startMove(['R1'], [0, 0], /*viaDrag=*/true, /*freshlyPasted=*/false);
    updateMove([0, 0]);
    commitMove();
  }
  return { before, after: editHistory.length };
});
assert(d.after === d.before,
       `three no-op clicks don't grow history (${d.before} → ${d.after})`);

await browser.close();
process.exit(summary(errors));
