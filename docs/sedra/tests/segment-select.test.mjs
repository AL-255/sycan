// Segment selection + `u` net-extend:
//   * click on a multi-segment wire picks the nearest segment.
//   * `u` expands to every wire in the connected component (parts
//     are *not* included — wires-only behaviour).
//   * Esc clears.
import { setup, assert, summary } from './_helpers.mjs';

const { browser, page, errors } = await setup();

await page.evaluate(() => {
  state.parts = []; state.wires = []; state.nameCounters = {}; state.nextId = 1;
  state.selectedIds.clear();
  state.pan = { x: 80, y: 200 }; state.zoom = 1;

  addPart('vsrc', 0,   0, 90);
  addPart('res',  240, 0, 90);
  addPart('cap',  480, 0, 90);
  addPart('gnd',  640, 0, 90);

  // W1 has multiple segments to test the "closest segment" pick.
  state.wires.push({ id: 'W1',
    points: [[40, 0], [120, 0], [120, -40], [200, -40], [200, 0]] });
  state.wires.push({ id: 'W2', points: [[280, 0], [440, 0]] });
  state.wires.push({ id: 'W3', points: [[520, 0], [640, 0]] });
  state.nextId = 4;
  pushHistory(); render();
});

// Synthesise a click in select-tool space — same code path as the
// canvas's mousedown handler.
async function click(wx, wy) {
  await page.evaluate(({ wx, wy }) => {
    state.tool = 'select';
    const w = document.getElementById('canvas-wrap');
    const rect = w.getBoundingClientRect();
    const screenX = rect.left + state.pan.x + wx * state.zoom;
    const screenY = rect.top  + state.pan.y + wy * state.zoom;
    const ev = (type) => new MouseEvent(type, {
      clientX: screenX, clientY: screenY, button: 0, bubbles: true,
    });
    w.dispatchEvent(ev('mousedown'));
    w.dispatchEvent(ev('mouseup'));
  }, { wx, wy });
}

// (1) Click W1 segment 0 and then segment 2; the singular click
// always sets exactly one entry in `selectedSegments`.
await click(80, 0);
let s = await page.evaluate(() => ({
  segs: [...state.selectedSegments], ids: [...state.selectedIds],
}));
assert(JSON.stringify(s.segs) === '["W1|0"]',
       'click in W1 segment 0 sets selectedSegments to ["W1|0"]');
assert(JSON.stringify(s.ids) === '["W1"]', 'wire id is in selectedIds');

await click(160, -40);
s = await page.evaluate(() => [...state.selectedSegments]);
assert(JSON.stringify(s) === '["W1|2"]',
       'click in W1 segment 2 replaces the marker with W1|2');

// (2) `u` expands to wires only — parts NOT included. The
// segment Set is repopulated with one entry per segment of every
// extended wire (the editor has no whole-wire selection state).
await page.evaluate(() => {
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'u', bubbles: true }));
});
const ext = await page.evaluate(() => ({
  ids: [...state.selectedIds].sort(),
  segs: [...state.selectedSegments].sort(),
}));
assert(JSON.stringify(ext.ids) === '["W1"]',
       `'u' extend includes wires only (got ${JSON.stringify(ext.ids)})`);
assert(JSON.stringify(ext.segs) === '["W1|0","W1|1","W1|2","W1|3"]',
       `'u' marks every segment of the promoted wire (got ${JSON.stringify(ext.segs)})`);

// (3) Click W2 and extend — that net is just W2.
await click(360, 0);
await page.evaluate(() => {
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'u', bubbles: true }));
});
const ext2 = await page.evaluate(() => [...state.selectedIds].sort());
assert(JSON.stringify(ext2) === '["W2"]', "'u' on W2 extends to {W2}");

// (4) Esc clears.
await page.evaluate(() => {
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
});
const cleared = await page.evaluate(() => ({
  ids: [...state.selectedIds], segs: [...state.selectedSegments],
}));
assert(cleared.ids.length === 0 && cleared.segs.length === 0,
       'Esc clears both selectedIds and selectedSegments');

await browser.close();
process.exit(summary(errors));
