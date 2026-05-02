// `pushHistory`'s end-to-end wire merging + interior simplification:
// after committing, every "wire" object should span end-to-end,
// terminating only at a 90° turn, a Steiner point (T-junction), or a
// free endpoint.
import { setup, assertEqual, summary, fmtWire } from './_helpers.mjs';

const { browser, page, errors } = await setup();

const seedAndPush = (parts, wires) => page.evaluate(({ parts, wires }) => {
  state.parts = []; state.wires = []; state.nameCounters = {}; state.nextId = 1;
  state.selectedIds.clear();
  for (const p of parts) addPart(p.type, p.x, p.y, p.rot || 0);
  for (const w of wires) state.wires.push({ ...w });
  state.nextId = wires.length + 1;
  pushHistory(); render();
  return state.wires.map(w => ({ id: w.id, points: w.points, label: w.label }));
}, { parts, wires });

// (1) Two collinear wires meeting at a free seam → fuse end-to-end.
let r = await seedAndPush([], [
  { id: 'A', points: [[0, 0], [40, 0]] },
  { id: 'B', points: [[40, 0], [80, 0]] },
]);
assertEqual(r, [{ id: 'A', points: [[0, 0], [80, 0]], label: undefined }],
            'collinear pair with free seam fuses to one wire');

// (2) T at the seam (third wire ends there) → all three preserved.
r = await seedAndPush([], [
  { id: 'A', points: [[0, 0], [40, 0]] },
  { id: 'B', points: [[40, 0], [80, 0]] },
  { id: 'C', points: [[40, 0], [40, 40]] },
]);
assertEqual(r.length, 3, 'T-junction at seam preserves all three wires');

// (3) Terminal at the seam → no merge.
r = await seedAndPush(
  [{ type: 'res', x: 40, y: 0, rot: 90 }],   // terminals at (0,0) and (80,0)
  [
    { id: 'A', points: [[0, 0], [80, 0]] },
    { id: 'B', points: [[80, 0], [160, 0]] },
  ],
);
assertEqual(r.length, 2, 'part terminal at seam blocks merge');

// (4) 90° turn at the seam → no merge.
r = await seedAndPush([], [
  { id: 'A', points: [[0, 0], [40, 0]] },
  { id: 'B', points: [[40, 0], [40, 40]] },
]);
assertEqual(r.length, 2, '90° meeting at seam preserves both wires');

// (5) Single wire with a redundant collinear interior vertex → simplified.
r = await seedAndPush([], [
  { id: 'A', points: [[0, 0], [40, 0], [80, 0]] },
]);
assertEqual(r[0].points, [[0, 0], [80, 0]],
            'redundant collinear interior vertex is dropped');

// (6) 90° corner stays.
r = await seedAndPush([], [
  { id: 'A', points: [[0, 0], [40, 0], [40, 40]] },
]);
assertEqual(r[0].points, [[0, 0], [40, 0], [40, 40]],
            '90° corner is preserved');

// (7) Three collinear segments end-to-end → fuse all.
r = await seedAndPush([], [
  { id: 'A', points: [[0, 0], [40, 0]] },
  { id: 'B', points: [[40, 0], [80, 0]] },
  { id: 'C', points: [[80, 0], [120, 0]] },
]);
assertEqual(r, [{ id: 'A', points: [[0, 0], [120, 0]], label: undefined }],
            'three collinear segments fuse end-to-end');

// (8) Label inheritance — the merged wire keeps the labelled side's name.
r = await seedAndPush([], [
  { id: 'A', points: [[0, 0], [40, 0]] },
  { id: 'B', points: [[40, 0], [80, 0]], label: 'out' },
]);
assertEqual(r.length, 1, 'collinear merge with one labelled side');
assertEqual(r[0].label, 'out', 'merged wire keeps the label from the labelled side');

await browser.close();
process.exit(summary(errors));
