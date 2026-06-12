// Select-tool gesture state machine: drag threshold, click vs drag
// separation, multi-selection narrowing, modifier-drag, off-canvas
// release, and Esc-mid-drag. These are the contracts of the
// KiCad-style press → threshold → move|marquee rewrite.
import { setup, assert, summary } from './_helpers.mjs';

const { browser, page, errors } = await setup();

// Two resistors and one two-segment wire, select tool armed.
const reset = () => page.evaluate(() => {
  state.parts = []; state.wires = []; state.nameCounters = {}; state.nextId = 1;
  state.selectedIds.clear(); state.selectedSegments.clear();
  state.pan = { x: 200, y: 200 }; state.zoom = 1; state.tool = 'select';
  if (state.moveDraft) cancelMove();
  state.boxSelect = null;
  addPart('res', 0,   0,   90);   // R1, body bbox around (0, 0)
  addPart('res', 200, 0,   90);   // R2
  state.wires.push({ id: 'W1', points: [[40, 0], [120, 0], [120, -80]] });
  state.nextId = 2;
  document.getElementById('drag-mode').checked = false;
  document.getElementById('parity-check').checked = false;
  pushHistory(); render();
});

await reset();   // sets pan before we measure the world→screen offset
const wrap = await page.evaluate(() => {
  const r = document.getElementById('canvas-wrap').getBoundingClientRect();
  return { left: r.left, top: r.top, w: r.width, h: r.height,
           px: state.pan.x, py: state.pan.y };
});
const sx = (x) => wrap.left + wrap.px + x;
const sy = (y) => wrap.top + wrap.py + y;

const peek = () => page.evaluate(() => ({
  ids: [...state.selectedIds].sort(),
  segs: [...state.selectedSegments].sort(),
  R1: (p => ({ x: p.x, y: p.y }))(state.parts.find(p => p.id === 'R1')),
  R2: (p => ({ x: p.x, y: p.y }))(state.parts.find(p => p.id === 'R2')),
  wireCount: state.wires.length,
  moveActive: state.moveDraft !== null,
}));

// (1) Sub-threshold jitter while clicking a part is a click, not a
// move: the part stays put even though the cursor wobbled 3px.
await reset();
await page.mouse.move(sx(0), sy(0));
await page.mouse.down();
await page.mouse.move(sx(0) + 3, sy(0) + 2);   // < 5px threshold
await page.mouse.up();
let s = await peek();
assert(s.R1.x === 0 && s.R1.y === 0,
       `3px jitter does not move the part (got (${s.R1.x}, ${s.R1.y}))`);
assert(JSON.stringify(s.ids) === '["R1"]', 'jittered click still selects');
assert(!s.moveActive, 'no move-draft left behind');

// (2) A plain click never engages the move engine — with a partial
// segment selection on W1, clicking the selected segment must not
// split the wire (the old implementation split on mousedown and
// relied on a zero-delta restore).
await reset();
await page.evaluate(() => {
  state.selectedIds.add('W1');
  state.selectedSegments.add('W1|0');
  render();
});
await page.mouse.click(sx(80), sy(0));   // on segment W1|0
s = await peek();
assert(s.wireCount === 1, `click does not split wires (got ${s.wireCount})`);
assert(JSON.stringify(s.segs) === '["W1|0"]',
       `click keeps the segment selection (got ${JSON.stringify(s.segs)})`);

// (3) Drag past the threshold from an UNSELECTED part selects it and
// moves it by the full delta.
await reset();
await page.mouse.move(sx(0), sy(0));
await page.mouse.down();
await page.mouse.move(sx(80), sy(40), { steps: 4 });
await page.mouse.up();
s = await peek();
assert(s.R1.x === 80 && s.R1.y === 40,
       `drag-from-unselected moves R1 to (80, 40) (got (${s.R1.x}, ${s.R1.y}))`);
assert(JSON.stringify(s.ids) === '["R1"]', 'dragged part ends up selected');
assert(!s.moveActive, 'drag commits on mouseup');

// (4) Plain click on a member of a multi-selection narrows to it;
// dragging a member moves the whole group.
await reset();
await page.evaluate(() => {
  state.selectedIds.add('R1');
  state.selectedIds.add('R2');
  render();
});
await page.mouse.move(sx(200), sy(0));
await page.mouse.down();
await page.mouse.move(sx(200), sy(80), { steps: 4 });
await page.mouse.up();
s = await peek();
assert(s.R1.y === 80 && s.R2.y === 80,
       `dragging a selected member moves the whole group ` +
       `(R1.y=${s.R1.y}, R2.y=${s.R2.y})`);
await page.mouse.click(sx(200), sy(80));   // plain click on R2
s = await peek();
assert(JSON.stringify(s.ids) === '["R2"]',
       `plain click narrows a multi-selection to the clicked item ` +
       `(got ${JSON.stringify(s.ids)})`);

// (5) Shift+drag from an unselected part adds it and moves both.
await reset();
await page.evaluate(() => { state.selectedIds.add('R1'); render(); });
await page.keyboard.down('Shift');
await page.mouse.move(sx(200), sy(0));
await page.mouse.down();
await page.mouse.move(sx(200), sy(60), { steps: 4 });
await page.mouse.up();
await page.keyboard.up('Shift');
s = await peek();
assert(s.R1.y === 60 && s.R2.y === 60,
       `shift+drag adds the pressed part and moves the union ` +
       `(R1.y=${s.R1.y}, R2.y=${s.R2.y})`);

// (6) Modifier+click on empty space keeps the selection.
await reset();
await page.evaluate(() => { state.selectedIds.add('R1'); render(); });
await page.keyboard.down('Shift');
await page.mouse.click(sx(500), sy(300));
await page.keyboard.up('Shift');
s = await peek();
assert(JSON.stringify(s.ids) === '["R1"]',
       'shift+click on empty space does not clear the selection');
await page.mouse.click(sx(500), sy(300));
s = await peek();
assert(s.ids.length === 0, 'plain click on empty space clears');

// (7) Releasing the button OUTSIDE the canvas still finishes the
// drag (window-level listeners) — no stuck move-draft.
await reset();
await page.mouse.move(sx(0), sy(0));
await page.mouse.down();
// Drag far enough to leave the canvas-wrap entirely (toolbar strip
// above the canvas at y < wrap.top).
await page.mouse.move(sx(80), wrap.top - 30, { steps: 6 });
await page.mouse.up();
s = await peek();
assert(!s.moveActive, 'off-canvas release does not strand the move-draft');
assert(s.R1.x === 80, `off-canvas release still committed the dx (got ${s.R1.x})`);

// (8) Esc mid-drag cancels: part returns to origin and the
// trailing mouseup does not re-commit.
await reset();
await page.mouse.move(sx(0), sy(0));
await page.mouse.down();
await page.mouse.move(sx(80), sy(40), { steps: 4 });
await page.keyboard.press('Escape');
await page.mouse.up();
s = await peek();
assert(s.R1.x === 0 && s.R1.y === 0,
       `Esc mid-drag restores the part (got (${s.R1.x}, ${s.R1.y}))`);
assert(!s.moveActive, 'Esc mid-drag leaves no move-draft');

// (9) Esc mid-marquee drops the box without applying it.
await reset();
await page.mouse.move(sx(-60), sy(-60));
await page.mouse.down();
await page.mouse.move(sx(260), sy(60), { steps: 4 });
await page.keyboard.press('Escape');
await page.mouse.up();
s = await peek();
const boxGone = await page.evaluate(() => state.boxSelect === null);
assert(boxGone && s.ids.length === 0,
       `Esc mid-marquee drops the box unapplied (ids=${JSON.stringify(s.ids)})`);

await browser.close();
process.exit(summary(errors));
