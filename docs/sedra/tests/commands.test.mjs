// Iteration 2: command registry, right-click context menu, Ctrl+K
// palette, status bar zones, zoom commands, undo/redo disabled state.
import { setup, assert, summary } from './_helpers.mjs';

const { browser, page, errors } = await setup();

const reset = () => page.evaluate(() => {
  state.parts = []; state.wires = []; state.nameCounters = {}; state.nextId = 1;
  state.selectedIds.clear(); state.selectedSegments.clear();
  state.pan = { x: 200, y: 200 }; state.zoom = 1; state.tool = 'select';
  if (state.moveDraft) cancelMove();
  closeContextMenu(); closeCmdPalette();
  addPart('res', 0, 0, 90);      // R1
  addPart('res', 200, 0, 90);    // R2
  state.wires.push({ id: 'W1', points: [[40, 0], [160, 0]] });
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

// (1) Registry invariants.
const reg = await page.evaluate(() => {
  const ids = COMMANDS.map(c => c.id);
  const sections = new Set(['part', 'wire', 'net', 'canvas']);
  return {
    unique: new Set(ids).size === ids.length,
    sectionsOk: COMMANDS.every(c => !c.menu || sections.has(c.menu.section)),
    cheatHaveShortcuts: COMMANDS.every(c => !c.cheat || !!c.shortcut),
    sheetRows: shortcutGroups().flatMap(g => g.rows.map(r => r[0])),
  };
});
assert(reg.unique, 'command ids are unique');
assert(reg.sectionsOk, 'menu sections are all known');
assert(reg.cheatHaveShortcuts, 'every cheat row has a shortcut');
assert(reg.sheetRows.includes('Ctrl+K') && reg.sheetRows.includes('Right-click'),
       'cheat sheet gained Ctrl+K and Right-click rows');

// (2) Fuzzy matcher.
const fz = await page.evaluate(() => ({
  fv: fuzzyMatch('fv', 'Fit view'),
  reject: fuzzyMatch('xyz', 'Fit view'),
  wordStart: (fuzzyMatch('fit', 'Fit view')?.score ?? 0)
           > (fuzzyMatch('it', 'Fit view')?.score ?? 0),
}));
assert(fz.fv && fz.fv.indices.length === 2, "fuzzy 'fv' matches Fit view");
assert(fz.reject === null, 'non-subsequence rejected');
assert(fz.wordStart, 'word-start bonus ranks higher');

// (3) Right-click on an unselected part collapses the selection to it
// and opens a menu with Edit value… / Delete.
await reset();
await page.mouse.click(sx(0), sy(0), { button: 'right' });
let menu = await page.evaluate(() => ({
  open: !!document.querySelector('.ctx-menu'),
  sel: [...state.selectedIds],
  labels: [...document.querySelectorAll('.ctx-label')].map(n => n.textContent),
}));
assert(menu.open, 'right-click opens the context menu');
assert(JSON.stringify(menu.sel) === '["R1"]',
       `right-click re-targets selection (got ${JSON.stringify(menu.sel)})`);
assert(menu.labels.includes('Edit value…') && menu.labels.some(l => l.startsWith('Delete')),
       `part menu has expected items (got ${JSON.stringify(menu.labels)})`);

// (4) Esc closes; right-click on a selected part keeps multi-selection.
await page.keyboard.press('Escape');
let open = await page.evaluate(() => !!document.querySelector('.ctx-menu'));
assert(!open, 'Esc closes the context menu');
await page.evaluate(() => {
  state.selectedIds.add('R1'); state.selectedIds.add('R2'); render();
});
await page.mouse.click(sx(0), sy(0), { button: 'right' });
menu = await page.evaluate(() => ({
  sel: [...state.selectedIds].sort(),
  labels: [...document.querySelectorAll('.ctx-label')].map(n => n.textContent),
}));
assert(JSON.stringify(menu.sel) === '["R1","R2"]',
       'right-click on selected member keeps the multi-selection');
assert(menu.labels.some(l => l === 'Delete 2 items'),
       `delete title carries the count (got ${JSON.stringify(menu.labels)})`);
await page.keyboard.press('Escape');

// (5) Right-drag pans and does NOT open the menu.
await reset();
const panBefore = await page.evaluate(() => ({ ...state.pan }));
await page.mouse.move(sx(400), sy(300));
await page.mouse.down({ button: 'right' });
await page.mouse.move(sx(480), sy(340), { steps: 4 });
await page.mouse.up({ button: 'right' });
const afterDrag = await page.evaluate(() => ({
  pan: { ...state.pan },
  menuOpen: !!document.querySelector('.ctx-menu'),
}));
assert(!afterDrag.menuOpen, 'right-drag does not open the menu');
assert(afterDrag.pan.x === panBefore.x + 80 && afterDrag.pan.y === panBefore.y + 40,
       `right-drag pans (got ${JSON.stringify(afterDrag.pan)})`);

// (6) Canvas menu: Paste here greyed with empty clipboard; Place
// recent caption present.
await page.evaluate(() => { clipboard = { parts: [], wires: [] }; });
await page.mouse.click(sx(500), sy(300), { button: 'right' });
menu = await page.evaluate(() => ({
  disabled: [...document.querySelectorAll('.ctx-item[aria-disabled="true"] .ctx-label')]
    .map(n => n.textContent),
  captions: [...document.querySelectorAll('.ctx-caption')].map(n => n.textContent),
}));
assert(menu.disabled.includes('Paste here'),
       'Paste here greyed when clipboard is empty');
assert(menu.captions.includes('Place recent'), 'canvas menu lists recent parts');
await page.keyboard.press('Escape');

// (7) Ctrl+K palette: opens, fuzzy-filters, Enter runs (Fit view).
await reset();
await page.keyboard.down('Control');
await page.keyboard.press('k');
await page.keyboard.up('Control');
open = await page.evaluate(() => !!document.querySelector('.cmdk-overlay'));
assert(open, 'Ctrl+K opens the palette');
await page.keyboard.type('fit view');
const palette = await page.evaluate(() => ({
  first: document.querySelector('.cmdk-item.active .cmdk-title')?.textContent ?? '',
}));
assert(palette.first.toLowerCase().includes('fit view'),
       `query ranks Fit view first (got "${palette.first}")`);
const zoomBefore = await page.evaluate(() => state.zoom);
await page.keyboard.press('Enter');
const afterRun = await page.evaluate(() => ({
  closed: !document.querySelector('.cmdk-overlay'),
  recents: JSON.parse(localStorage.getItem('sycan.sedra.cmdk.recent.v1') || '[]'),
}));
assert(afterRun.closed, 'Enter closes the palette');
assert(afterRun.recents[0] === 'view.fit', 'run command lands in recents');

// (8) Zoom commands + status bar zones.
await reset();
const zoom = await page.evaluate(() => {
  setZoom(1);
  const z0 = document.getElementById('sb-zoom-pct').textContent;
  setZoom(state.zoom * ZOOM_STEP);
  const z1 = document.getElementById('sb-zoom-pct').textContent;
  setZoom(9);   // clamps to ZOOM_MAX
  const zMax = state.zoom;
  setZoom(1);
  state.selectedIds.add('R1'); state.selectedIds.add('W1');
  selectWholeWire('W1');
  refreshHint();   // tests assign state.tool directly, bypassing setTool
  render();
  return {
    z0, z1, zMax,
    sel: document.getElementById('sb-sel').textContent,
    grid: document.getElementById('sb-grid').textContent,
    mode: document.getElementById('sb-mode').textContent,
  };
});
assert(zoom.z0 === '100%' && zoom.z1 === '141%',
       `zoom readout ticks (${zoom.z0} → ${zoom.z1})`);
assert(zoom.zMax === 4, 'zoom clamps at ZOOM_MAX');
assert(zoom.sel === '1 part, 1 wire',
       `selection summary zone (got "${zoom.sel}")`);
assert(zoom.grid === 'Grid 20', 'grid zone shows pitch');
assert(zoom.mode === 'SELECT' || zoom.mode === 'Select',
       `mode chip shows the tool (got "${zoom.mode}")`);

// (9) Shift+F zooms to selection.
const zts = await page.evaluate(() => {
  setZoom(0.5);
  document.dispatchEvent(new KeyboardEvent('keydown',
    { key: 'F', shiftKey: true, bubbles: true }));
  return state.zoom;
});
assert(zts > 0.5, `Shift+F zooms into the selection (zoom=${zts})`);

// (10) Undo/redo disabled states.
const ur = await page.evaluate(() => {
  state.parts = []; state.wires = []; state.nameCounters = {}; state.nextId = 1;
  state.selectedIds.clear(); state.selectedSegments.clear();
  editHistory.length = 0; historyIdx = -1;
  pushHistory();                       // baseline empty state
  const atBase = {
    undo: document.getElementById('btn-undo').disabled,
    redo: document.getElementById('btn-redo').disabled,
  };
  addPart('res', 0, 0, 0);
  pushHistory();
  const afterEdit = {
    undo: document.getElementById('btn-undo').disabled,
    redo: document.getElementById('btn-redo').disabled,
  };
  document.getElementById('btn-undo').click();
  const afterUndo = {
    undo: document.getElementById('btn-undo').disabled,
    redo: document.getElementById('btn-redo').disabled,
  };
  return { atBase, afterEdit, afterUndo };
});
assert(ur.atBase.undo && ur.atBase.redo, 'both disabled at baseline');
assert(!ur.afterEdit.undo && ur.afterEdit.redo, 'undo enabled after an edit');
assert(!ur.afterUndo.redo, 'redo enabled after undo');

await browser.close();
process.exit(summary(errors));
