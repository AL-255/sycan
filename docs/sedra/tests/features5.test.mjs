// Iteration 5: KiCad marquee window/crossing semantics, multi-net
// highlight pinning, one-shot flash halos.
import { setup, assert, summary } from './_helpers.mjs';

const { browser, page, errors } = await setup();

const reset = () => page.evaluate(() => {
  state.parts = []; state.wires = []; state.nameCounters = {}; state.nextId = 1;
  state.selectedIds.clear(); state.selectedSegments.clear();
  pinnedNets = []; updateNetLegend();
  state.pan = { x: 200, y: 200 }; state.zoom = 1; setTool('select');
  addPart('res', 0, 0, 90);      // R1 spans x -40..40
  addPart('res', 300, 0, 90);    // R2
  state.wires.push({ id: 'W1', points: [[40, 0], [260, 0]] });
  state.nextId = 2;
  pushHistory(); render();
});

await reset();
const wb = await page.evaluate(() => {
  const r = document.getElementById('canvas-wrap').getBoundingClientRect();
  return { left: r.left, top: r.top, px: state.pan.x, py: state.pan.y };
});
const sx = (x) => wb.left + wb.px + x;
const sy = (y) => wb.top + wb.py + y;
const dragBox = async (x0, y0, x1, y1) => {
  await page.mouse.move(sx(x0), sy(y0));
  await page.mouse.down();
  await page.mouse.move(sx(x1), sy(y1), { steps: 5 });
  await page.mouse.up();
};

// (1) Window select (L→R): a box clipping only R1's edge selects
// nothing (centre containment), and a box around R1 selects only R1.
await dragBox(-60, -60, -30, 60);     // grazes R1's left edge only
let s = await page.evaluate(() => [...state.selectedIds]);
assert(s.length === 0, `window graze selects nothing (got ${JSON.stringify(s)})`);
await dragBox(-80, -80, 80, 80);
s = await page.evaluate(() => [...state.selectedIds]);
assert(JSON.stringify(s) === '["R1"]', `window containment (got ${JSON.stringify(s)})`);

// (2) Crossing select (R→L): the same grazing box now catches R1,
// and a box crossing the middle of W1 catches the wire too.
await reset();
await dragBox(-30, 60, -60, -60);     // R→L = crossing, touches R1 bbox
s = await page.evaluate(() => [...state.selectedIds]);
assert(JSON.stringify(s) === '["R1"]', `crossing graze selects R1 (got ${JSON.stringify(s)})`);
await reset();
await dragBox(160, 40, 120, -40);     // small R→L box over W1's middle
s = await page.evaluate(() => ({
  ids: [...state.selectedIds],
  segs: [...state.selectedSegments],
}));
assert(s.ids.includes('W1') && s.segs.length > 0,
       `crossing box catches the wire it intersects (got ${JSON.stringify(s)})`);

// (3) Crossing marquee renders dashed in --ok; window solid --select.
const visuals = await page.evaluate(() => {
  state.boxSelect = { x0: 0, y0: 0, x1: 100, y1: 80, additive: false };
  render();
  const win = document.querySelector('#canvas rect[stroke="var(--select)"]');
  const winDash = win?.getAttribute('stroke-dasharray');
  state.boxSelect = { x0: 100, y0: 0, x1: 0, y1: 80, additive: false };
  render();
  const cross = document.querySelector('#canvas rect[stroke="var(--ok)"]');
  const crossDash = cross?.getAttribute('stroke-dasharray');
  state.boxSelect = null; render();
  return { win: !!win, winDash, cross: !!cross, crossDash };
});
assert(visuals.win && visuals.winDash === 'none', 'window box solid blue');
assert(visuals.cross && visuals.crossDash === '5 4', 'crossing box dashed green');

// (4) Net pinning: pin two nets, legend chips appear, unpin works.
await reset();
const pin = await page.evaluate(() => {
  // resolve + pin net at W1, then the R2 right-terminal net
  finalizeNetHighlight([100, 0], [100, 0]);
  pinCurrentNetHighlight();
  finalizeNetHighlight([340, 0], [340, 0]);
  pinCurrentNetHighlight();
  const chips = [...document.querySelectorAll('.net-chip')].map(c => c.textContent);
  const washes = document.querySelectorAll(
    '.net-highlight, .net-highlight-term').length;
  document.querySelector('.net-chip').click();
  const chipsAfter = document.querySelectorAll('.net-chip').length;
  return { pinned: pinnedNets.length + 1, chips, washes, chipsAfter };
});
assert(pin.chips.length === 2, `two legend chips (got ${JSON.stringify(pin.chips)})`);
assert(pin.washes >= 2, 'pinned nets draw washes');
assert(pin.chipsAfter === 1, 'clicking a chip unpins');

// (5) Flash halos: duplicate flashes the clones, halo expires.
await reset();
const flash = await page.evaluate(async () => {
  state.selectedIds.add('R1');
  duplicateSelection();
  render();
  const during = document.querySelectorAll('.flash-halo').length;
  await new Promise(r => setTimeout(r, 800));
  render();
  const after = document.querySelectorAll('.flash-halo').length;
  return { during, after };
});
assert(flash.during > 0, 'duplicate draws a flash halo');
assert(flash.after === 0, 'halo expires after ~600ms');

await browser.close();
process.exit(summary(errors));
