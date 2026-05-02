// Selection: click picks one segment, shift adds, ctrl toggles, box
// picks segments fully inside, `u` extends to wires-only, Esc clears.
import { setup, assert, summary } from './_helpers.mjs';

const { browser, page, errors } = await setup();

await page.evaluate(() => {
  state.parts = []; state.wires = []; state.nameCounters = {}; state.nextId = 1;
  state.selectedIds.clear(); state.selectedSegments.clear();
  state.pan = { x: 200, y: 200 }; state.zoom = 1; state.tool = 'select';
  addPart('res', 0,   0, 90);   // R1
  addPart('res', 200, 0, 90);   // R2 (centre at x=200)
  // W1 is an over-the-top loop: 4 segments. Segments 0–3:
  //  0: (40,0)→(120,0) horiz   1: (120,0)→(120,-80) vert
  //  2: (120,-80)→(200,-80) horiz   3: (200,-80)→(200,0) vert
  state.wires.push({ id: 'W1', points: [[40, 0], [120, 0], [120, -80], [200, -80], [200, 0]] });
  state.wires.push({ id: 'W2', points: [[400, 0], [560, 0]] });
  state.nextId = 3;
  pushHistory(); render();
});

const wrap = await page.evaluate(() => {
  const r = document.getElementById('canvas-wrap').getBoundingClientRect();
  return { left: r.left, top: r.top, px: state.pan.x, py: state.pan.y };
});
const sx = (x) => wrap.left + wrap.px + x;
const sy = (y) => wrap.top + wrap.py + y;

async function click(wx, wy, { shift, ctrl } = {}) {
  if (shift) await page.keyboard.down('Shift');
  if (ctrl)  await page.keyboard.down('Control');
  await page.mouse.click(sx(wx), sy(wy));
  if (ctrl)  await page.keyboard.up('Control');
  if (shift) await page.keyboard.up('Shift');
}

async function dragBox(wx0, wy0, wx1, wy1, { shift } = {}) {
  if (shift) await page.keyboard.down('Shift');
  await page.mouse.move(sx(wx0), sy(wy0));
  await page.mouse.down();
  await page.mouse.move(sx(wx1), sy(wy1), { steps: 6 });
  await page.mouse.up();
  if (shift) await page.keyboard.up('Shift');
}

const peek = () => page.evaluate(() => ({
  ids: [...state.selectedIds].sort(),
  segs: [...state.selectedSegments].sort(),
}));
const press = (key) => page.evaluate((k) =>
  document.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true })), key);

// (1) Click on the vertical at x=120 (segment 1) picks just that segment.
await click(120, -40);
let s = await peek();
assert(JSON.stringify(s.segs) === '["W1|1"]',
       `click picks the nearest segment (got ${JSON.stringify(s.segs)})`);
assert(JSON.stringify(s.ids) === '["W1"]', 'wire id added to selectedIds');

// (2) Shift+click R1's centre adds it without dropping the wire selection.
await click(0, 0, { shift: true });
s = await peek();
assert(s.ids.includes('R1') && s.ids.includes('W1') && s.segs.includes('W1|1'),
       `shift+click extends across wire+part (got ids=${JSON.stringify(s.ids)})`);

// (3) Shift+click R1 again is a no-op (additive only).
await click(0, 0, { shift: true });
s = await peek();
assert(s.ids.includes('R1'), 'shift+click is idempotent (never removes)');

// (4) Ctrl+click R1 toggles it off.
await click(0, 0, { ctrl: true });
s = await peek();
assert(!s.ids.includes('R1'), `ctrl+click toggles off (got ${JSON.stringify(s.ids)})`);

// (5) Esc clears.
await press('Escape');
s = await peek();
assert(s.ids.length === 0 && s.segs.length === 0, 'Esc clears selection');

// (6) Box covers W1's left half — segments 0 and 1. Start x>0 so
// R1's origin at (0, 0) doesn't get picked up alongside the wire.
await dragBox(20, -90, 130, 10);
s = await peek();
assert(JSON.stringify(s.segs) === '["W1|0","W1|1"]'
       && JSON.stringify(s.ids) === '["W1"]',
       `partial box selects segments fully inside ` +
       `(segs=${JSON.stringify(s.segs)}, ids=${JSON.stringify(s.ids)})`);

// (7) Shift+box extends instead of replacing.
await dragBox(380, -10, 580, 20, { shift: true });
s = await peek();
assert(s.segs.includes('W1|0') && s.segs.includes('W2|0'),
       `shift+box extends existing selection (got ${JSON.stringify(s.segs)})`);

// (8) `u` promotes W2's segment selection to its connected component.
// In this layout W2 is its own net so the result is just W2.
await press('Escape');
await click(480, 0);
await press('u');
s = await peek();
assert(JSON.stringify(s.ids) === '["W2"]',
       `'u' extends to wires only (got ${JSON.stringify(s.ids)})`);

await browser.close();
process.exit(summary(errors));
