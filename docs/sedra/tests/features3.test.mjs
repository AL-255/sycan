// Iteration 3: flip/mirror, SVG export, ERC overlay, wire-draft power
// keys, adaptive grid toggle, collapsible panels, undoable Clear.
import { setup, assert, summary } from './_helpers.mjs';

const { browser, page, errors } = await setup();

const reset = () => page.evaluate(() => {
  state.parts = []; state.wires = []; state.nameCounters = {}; state.nextId = 1;
  state.selectedIds.clear(); state.selectedSegments.clear();
  state.pan = { x: 200, y: 200 }; state.zoom = 1; setTool('select');
  addPart('diode', 0, 0, 0);     // D1 — asymmetric, flip-visible
  addPart('gnd', 200, 100, 0);
  pushHistory(); render();
});

// (1) Flip mirrors terminals about the part's own axis and round-trips
// through duplicate.
await reset();
const flip = await page.evaluate(() => {
  const before = partTerminals(state.parts[0]).map(t => t.pos[0]);
  state.selectedIds.add('D1');
  flipSelection();
  const after = partTerminals(state.parts[0]).map(t => t.pos[0]);
  duplicateSelection();
  const clone = state.parts.find(p => p.id === 'D2');
  return { before, after, flip: !!state.parts[0].flip, cloneFlip: !!clone?.flip };
});
assert(flip.flip, 'flipSelection sets the flag');
assert(JSON.stringify(flip.after) === JSON.stringify(flip.before.map(x => -x)),
       `terminals mirror (${flip.before} → ${flip.after})`);
assert(flip.cloneFlip, 'duplicate carries flip');

// (2) SVG export: standalone markup, no editor layers, token-inlined.
const svg = await page.evaluate(() => exportSchematicSvg());
assert(svg && svg.includes('<svg'), 'export produces SVG markup');
assert(!svg.includes('hit-layer') && !svg.includes('data-layer="grid"'),
       'editor-only layers stripped');
assert(!svg.includes('var(--stroke)') && svg.includes('.wire {'),
       'token values inlined via embedded style');

// (3) ERC: unconnected terminals + missing ground reported; resolving
// clears them.
const erc = await page.evaluate(() => {
  state.parts = []; state.wires = []; state.nameCounters = {}; state.nextId = 1;
  state.selectedIds.clear(); state.selectedSegments.clear();
  addPart('res', 0, 0, 90);              // R1, floating, no gnd anywhere
  pushHistory(); render();
  const withFindings = {
    count: ercCache.length,
    hasGroundError: ercCache.some(f => f.level === 'error'),
    badge: !!document.querySelector('.erc-marker'),
    zone: document.getElementById('sb-erc').textContent,
  };
  // Wire both terminals to a grounded net.
  addPart('gnd', 0, 160, 0);
  state.wires.push({ id: 'W1', points: [[-40, 0], [-40, 160], [0, 160]] });
  state.wires.push({ id: 'W2', points: [[40, 0], [40, 160], [0, 160]] });
  pushHistory(); render();
  return { withFindings, after: ercCache.length };
});
assert(erc.withFindings.count >= 3 && erc.withFindings.hasGroundError,
       `floating part reports terminal + ground findings (${erc.withFindings.count})`);
assert(erc.withFindings.badge && /error|warning/.test(erc.withFindings.zone),
       `markers + status zone render (zone="${erc.withFindings.zone}")`);
assert(erc.after === 0, 'connecting everything clears the findings');

// (4) Wire-draft power keys: '/' flips posture, Enter finishes,
// Backspace pops.
const wire = await page.evaluate(() => {
  state.parts = []; state.wires = []; state.nameCounters = {}; state.nextId = 1;
  setTool('WIRE');
  handleWireClick([0, 0], [0, 0]);
  const posture0 = state.wireDraft.axisFirst;
  document.dispatchEvent(new KeyboardEvent('keydown', { key: '/', bubbles: true }));
  const posture1 = state.wireDraft.axisFirst;
  handleWireClick([80, 40], [80, 40]);
  const cornersBefore = state.wireDraft.points.length;
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Backspace', bubbles: true }));
  const cornersAfter = state.wireDraft.points.length;
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
  return { posture0, posture1, cornersBefore, cornersAfter,
           draftGone: state.wireDraft === null,
           wireCount: state.wires.length };
});
assert(wire.posture1 !== wire.posture0, "'/' flips the preview posture");
assert(wire.cornersAfter < wire.cornersBefore, 'Backspace pops a corner');
assert(wire.draftGone && wire.wireCount === 1, 'Enter finishes the wire');

// (5) Grid toggle persists; adaptive pitch at low zoom.
const grid = await page.evaluate(() => {
  setTool('select');
  setZoom(1);
  render();
  const visible = !!document.querySelector('[data-layer="grid"]');
  toggleGrid();
  const hidden = !document.querySelector('[data-layer="grid"]');
  const stored = localStorage.getItem('sycan.sedra.grid.v1');
  toggleGrid();   // restore
  setZoom(0.25);  // adaptive: pitch should widen (step > GRID)
  render();
  const d = document.querySelector('[data-layer="grid"]')?.getAttribute('d') ?? '';
  // Vertical strokes sit exactly on grid points: "M<x>,<y> v<len>".
  const xs = [...d.matchAll(/M([-\d.]+),[-\d.]+ v/g)].map(m => Number(m[1]));
  const uniq = [...new Set(xs)].sort((a, b) => a - b);
  const pitch = uniq.length > 1 ? uniq[1] - uniq[0] : 0;
  setZoom(1);
  return { visible, hidden, stored, pitch };
});
assert(grid.visible && grid.hidden && grid.stored === '0',
       'grid toggle hides and persists');
assert(grid.pitch >= 40, `adaptive grid widens pitch at low zoom (${grid.pitch})`);

// (6) Collapsible panels persist.
const pane = await page.evaluate(() => {
  const h3 = document.querySelector('#side h3');
  const target = h3.nextElementSibling;
  h3.click();
  const collapsed = target.style.display === 'none';
  const stored = localStorage.getItem('sycan.sedra.panes.v1') || '{}';
  h3.click();
  const reopened = target.style.display !== 'none';
  return { collapsed, reopened, stored: stored.includes('true') };
});
assert(pane.collapsed && pane.reopened && pane.stored,
       'panel sections collapse, persist, reopen');

// (7) Undoable Clear: no confirm dialog, toast carries Undo.
const clear = await page.evaluate(() => {
  state.parts = []; state.wires = []; state.nameCounters = {}; state.nextId = 1;
  addPart('res', 0, 0, 0);
  pushHistory(); render();
  document.getElementById('notifications').innerHTML = '';
  document.getElementById('btn-clear').click();
  const cleared = state.parts.length === 0;
  const toast = document.querySelector('.notification');
  const action = toast?.querySelector('.notification-action');
  action?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  return { cleared, hadAction: !!action, restored: state.parts.length === 1 };
});
assert(clear.cleared, 'Clear empties without confirm()');
assert(clear.hadAction && clear.restored, 'toast Undo restores the schematic');

await browser.close();
process.exit(summary(errors));
