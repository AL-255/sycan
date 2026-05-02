// Toolbar / status-bar / side-panel UI behaviour:
//   * `u` extends a segment selection to *wires only* (no parts).
//   * #hint stays at a fixed 2-line height regardless of content.
//   * #side-resizer drags the right-hand panel within its min/max bounds.
//   * #coords readout shows the snapped cursor — and switches to a
//     start→end pair while a box-select is in flight.
import { setup, assert, summary } from './_helpers.mjs';

const { browser, page, errors } = await setup();

// (1) wires-only `u` extend.
await page.evaluate(() => {
  state.parts = []; state.wires = []; state.nameCounters = {}; state.nextId = 1;
  state.selectedIds.clear();
  state.pan = { x: 80, y: 200 }; state.zoom = 1;
  addPart('vsrc', 0,   0, 90);     // V1, terminals (-40, 0), (40, 0)
  addPart('res',  200, 0, 90);     // R1, terminals (160, 0), (280, 0)
  state.wires.push({ id: 'W1', points: [[40, 0], [160, 0]] });
  state.nextId = 2;
  pushHistory(); render();

  state.tool = 'select';
  state.selectedSegments.clear();
  state.selectedSegments.add(`W1|0`);
  state.selectedIds = new Set(['W1']);
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'u', bubbles: true }));
});
const after = await page.evaluate(() => ({
  ids: [...state.selectedIds].sort(),
  segs: [...state.selectedSegments].sort(),
}));
assert(JSON.stringify(after.ids) === JSON.stringify(['W1']),
       `'u' selects only the wire (got ${JSON.stringify(after.ids)})`);
// W1 has one segment in this fixture; 'u' marks it.
assert(JSON.stringify(after.segs) === '["W1|0"]',
       `'u' populates per-segment markers (got ${JSON.stringify(after.segs)})`);

// (2) hint bar height = 2 lines + 8px padding (~44 px at 0.78rem * 1.45 line-height).
const hintHeight = await page.evaluate(() =>
  document.getElementById('hint').getBoundingClientRect().height);
assert(hintHeight >= 36 && hintHeight <= 60,
       `hint bar is two lines tall (~44 px; got ${hintHeight.toFixed(1)})`);

// (3) side-panel resize.
const widthBefore = await page.evaluate(() =>
  document.getElementById('side').getBoundingClientRect().width);
const handle = await page.$eval('#side-resizer', (el) => {
  const r = el.getBoundingClientRect();
  return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
});
await page.mouse.move(handle.x, handle.y);
await page.mouse.down();
await page.mouse.move(handle.x - 100, handle.y, { steps: 5 });
await page.mouse.up();
const widthAfter = await page.evaluate(() =>
  document.getElementById('side').getBoundingClientRect().width);
assert(widthAfter > widthBefore + 50,
       `dragging resizer left widens the side panel (${widthBefore}→${widthAfter})`);

// Drag back the other way and verify clamping.
const handle2 = await page.$eval('#side-resizer', (el) => {
  const r = el.getBoundingClientRect();
  return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
});
await page.mouse.move(handle2.x, handle2.y);
await page.mouse.down();
await page.mouse.move(handle2.x + 60, handle2.y, { steps: 5 });
await page.mouse.up();
const widthShrunk = await page.evaluate(() =>
  document.getElementById('side').getBoundingClientRect().width);
assert(widthShrunk < widthAfter && widthShrunk >= 200,
       `dragging back narrows it but stays above min-width (${widthAfter}→${widthShrunk})`);

// (4) Coordinate readout: bare cursor.
//     Move the mouse into the canvas at a known world point and
//     verify #coords reads the snapped grid coords.
await page.evaluate(() => {
  state.parts = []; state.wires = []; state.nameCounters = {}; state.nextId = 1;
  state.selectedIds.clear();
  state.tool = 'select';
  state.pan = { x: 200, y: 200 }; state.zoom = 1;
  render();
});
const wrapBox = await page.$eval('#canvas-wrap', (el) => {
  const r = el.getBoundingClientRect();
  return { left: r.left, top: r.top };
});
// World (100, 80) at pan (200,200) zoom 1 → screen (300, 280) inside wrap.
await page.mouse.move(wrapBox.left + 300, wrapBox.top + 280, { steps: 4 });
await new Promise(r => setTimeout(r, 50));
const cursorReadout = await page.$eval('#coords', (el) => ({
  text: el.textContent || '',
  hidden: el.classList.contains('hidden'),
}));
assert(!cursorReadout.hidden, 'coords readout is visible while cursor is on canvas');
assert(cursorReadout.text === '(100, 80)',
       `coords reads the snapped cursor (got "${cursorReadout.text}")`);

// (5) Coordinate readout: box-select pair.
//     Press the left mouse button on empty canvas and drag — the
//     readout swaps to "(start) → (end)".
await page.mouse.move(wrapBox.left + 300, wrapBox.top + 300);
await page.mouse.down();
await page.mouse.move(wrapBox.left + 460, wrapBox.top + 380, { steps: 4 });
const boxReadout = await page.$eval('#coords', (el) => el.textContent || '');
await page.mouse.up();
assert(/^\(\d+, \d+\)\s*→\s*\(\d+, \d+\)$/.test(boxReadout),
       `box-select readout has start→end pair (got "${boxReadout}")`);

// (6) Off-canvas cursor → readout hides.
await page.mouse.move(wrapBox.left + 300, wrapBox.top + 300);  // re-enter first
await page.mouse.move(wrapBox.left - 50, wrapBox.top - 50);    // exit
await new Promise(r => setTimeout(r, 50));
const offCanvas = await page.$eval('#coords', (el) =>
  el.classList.contains('hidden'));
assert(offCanvas, 'coords readout hides when the cursor leaves the canvas');

await browser.close();
process.exit(summary(errors));
