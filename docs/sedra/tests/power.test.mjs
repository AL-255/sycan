// Power-user features from critique iteration 1: Ctrl+D duplicate,
// arrow-key nudge, '?' cheat sheet, double-click inline value edit,
// hover pre-selection.
import { setup, assert, summary } from './_helpers.mjs';

const { browser, page, errors } = await setup();

const reset = () => page.evaluate(() => {
  state.parts = []; state.wires = []; state.nameCounters = {}; state.nextId = 1;
  state.selectedIds.clear(); state.selectedSegments.clear();
  state.pan = { x: 200, y: 200 }; state.zoom = 1; state.tool = 'select';
  document.getElementById('drag-mode').checked = true;
  document.getElementById('parity-check').checked = true;
  addPart('res', 0, 0, 90);     // R1: terminals (-40,0),(40,0)
  addPart('vsrc', -200, 0, 90); // V1
  state.wires.push({ id: 'W1', points: [[-160, 0], [-40, 0]] });
  state.nextId = 2;
  pushHistory(); render();
});

await reset();
const wrapBox = await page.evaluate(() => {
  const r = document.getElementById('canvas-wrap').getBoundingClientRect();
  return { left: r.left, top: r.top, px: state.pan.x, py: state.pan.y };
});
const sx = (x) => wrapBox.left + wrapBox.px + x;
const sy = (y) => wrapBox.top + wrapBox.py + y;
const press = (key, mods = {}) => page.evaluate(({ key, mods }) =>
  document.dispatchEvent(new KeyboardEvent('keydown',
    { key, bubbles: true, ...mods })), { key, mods });

// (1) Ctrl+D duplicates the selected part and selects the clone.
await reset();
await page.evaluate(() => { state.selectedIds.add('R1'); render(); });
await press('d', { ctrlKey: true });
let s = await page.evaluate(() => ({
  parts: state.parts.map(p => ({ id: p.id, x: p.x, y: p.y })),
  sel: [...state.selectedIds],
}));
assert(s.parts.length === 3, `Ctrl+D adds a clone (${s.parts.length} parts)`);
const clone = s.parts.find(p => p.id === 'R2');
assert(clone && clone.x === 40 && clone.y === 40,
       `clone offset one step down-right (got ${JSON.stringify(clone)})`);
assert(s.sel.length === 1 && s.sel[0] === 'R2', 'selection moves to the clone');

// (2) Arrow nudge moves the selection one grid step through the move
// engine — attached wires follow.
await reset();
await page.evaluate(() => { state.selectedIds.add('R1'); render(); });
await press('ArrowDown');
s = await page.evaluate(() => ({
  R1: (p => ({ x: p.x, y: p.y }))(state.parts.find(p => p.id === 'R1')),
  w1End: state.wires.find(w => w.id === 'W1')?.points.slice(-1)[0] ?? null,
  attached: state.wires.some(w =>
    w.points.some(p => p[0] === -40 && p[1] === 20)),
}));
assert(s.R1.y === 20, `ArrowDown nudges one grid step (y=${s.R1.y})`);
assert(s.attached, 'attached wire follows the nudge');

// (3) '?' opens the cheat sheet; Esc closes it.
await press('?');
let overlay = await page.evaluate(() =>
  !!document.querySelector('.shortcut-overlay'));
assert(overlay, "'?' opens the shortcut cheat sheet");
await press('Escape');
overlay = await page.evaluate(() =>
  !!document.querySelector('.shortcut-overlay'));
assert(!overlay, 'Esc closes the cheat sheet');

// (4) Double-click a part → inline value editor; Enter commits.
// (Puppeteer needs the explicit two-click sequence to synthesize a
// dblclick event.)
const dblclick = async (x, y) => {
  await page.mouse.click(x, y);
  await page.mouse.click(x, y, { clickCount: 2 });
};
await reset();
await dblclick(sx(0), sy(0));
let hasEditor = await page.evaluate(() => !!document.querySelector('.inline-edit'));
assert(hasEditor, 'double-click opens the inline value editor');
await page.keyboard.down('Control');
await page.keyboard.press('a');
await page.keyboard.up('Control');
await page.keyboard.type('47k');
await page.keyboard.press('Enter');
s = await page.evaluate(() => ({
  value: state.parts.find(p => p.id === 'R1').value,
  editorGone: !document.querySelector('.inline-edit'),
}));
assert(s.value === '47k', `Enter commits the value (got ${s.value})`);
assert(s.editorGone, 'editor closes on commit');

// (5) Esc cancels the inline editor without changing the value.
await dblclick(sx(0), sy(0));
await page.keyboard.type('999');
await page.keyboard.press('Escape');
s = await page.evaluate(() => ({
  value: state.parts.find(p => p.id === 'R1').value,
  editorGone: !document.querySelector('.inline-edit'),
}));
assert(s.value === '47k' && s.editorGone, 'Esc cancels the inline edit');

// (6) Hover pre-selection: moving over an unselected part shows the
// hover box; moving to empty space clears it.
await reset();
await page.mouse.move(sx(0), sy(0));
let hover = await page.evaluate(() => !!document.querySelector('.hover-box'));
assert(hover, 'hovering a part shows the pre-selection box');
await page.mouse.move(sx(400), sy(300));
hover = await page.evaluate(() => !!document.querySelector('.hover-box'));
assert(!hover, 'hover box clears on empty space');

await browser.close();
process.exit(summary(errors));
