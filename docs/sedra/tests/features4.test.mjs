// Iteration 4: select-similar, Tab cycling, renumber, ERC duplicate
// designators, wire snap indicator, starter card.
import { setup, assert, summary } from './_helpers.mjs';

const { browser, page, errors } = await setup();

const reset = () => page.evaluate(() => {
  state.parts = []; state.wires = []; state.nameCounters = {}; state.nextId = 1;
  state.selectedIds.clear(); state.selectedSegments.clear();
  state.pan = { x: 200, y: 200 }; state.zoom = 1; setTool('select');
  addPart('res', 0, 0, 90);      // R1
  addPart('cap', 200, 0, 90);    // C1
  addPart('res', 400, 0, 90);    // R2
  pushHistory(); render();
});

// (1) Select-similar picks every part of the hit type.
await reset();
let s = await page.evaluate(() => {
  selectSimilar('R1');
  return [...state.selectedIds].sort();
});
assert(JSON.stringify(s) === '["R1","R2"]', `select-similar (got ${JSON.stringify(s)})`);

// (2) Tab cycles parts in id order; Shift+Tab reverses.
await reset();
s = await page.evaluate(() => {
  const seq = [];
  cycleSelection(1); seq.push([...state.selectedIds][0]);
  cycleSelection(1); seq.push([...state.selectedIds][0]);
  cycleSelection(1); seq.push([...state.selectedIds][0]);
  cycleSelection(1); seq.push([...state.selectedIds][0]);   // wraps
  cycleSelection(-1); seq.push([...state.selectedIds][0]);
  return seq;
});
assert(JSON.stringify(s) === '["C1","R1","R2","C1","R2"]',
       `Tab cycling order (got ${JSON.stringify(s)})`);

// (3) Renumber: reading order, ctrlSrc remap, default values track.
const rn = await page.evaluate(() => {
  state.parts = []; state.wires = []; state.nameCounters = {}; state.nextId = 1;
  state.selectedIds.clear(); state.selectedSegments.clear();
  // Deliberately scrambled ids vs positions.
  addPart('res', 400, 0, 0);     // R1 at far right
  addPart('res', 0, 0, 0);       // R2 at origin (should become R1)
  addPart('cccs', 200, 0, 0);    // F1, controlled by old R1? use vsrc name
  const f = state.parts.find(p => p.type === 'cccs');
  f.ctrlSrc = 'R1';
  pushHistory();
  renumberParts();
  return {
    ids: state.parts.map(p => `${p.id}@${p.x}`).sort(),
    ctrl: state.parts.find(p => p.type === 'cccs').ctrlSrc,
  };
});
assert(rn.ids.includes('R1@0') && rn.ids.includes('R2@400'),
       `renumber follows reading order (got ${JSON.stringify(rn.ids)})`);
assert(rn.ctrl === 'R2', `ctrlSrc remapped to the renamed id (got ${rn.ctrl})`);

// (4) ERC flags duplicate designators.
const dup = await page.evaluate(() => {
  state.parts = []; state.wires = []; state.nameCounters = {}; state.nextId = 1;
  addPart('res', 0, 0, 0);
  addPart('gnd', 0, 160, 0);
  state.parts[0].id = 'X1';
  state.parts.push({ ...state.parts[0], x: 200 });   // duplicate X1
  pushHistory();
  return ercCache.some(f => f.msg.includes('Duplicate designator X1'));
});
assert(dup, 'ERC reports duplicate designators');

// (5) Wire-tool snap ring appears on connection targets only.
const snap = await page.evaluate(() => {
  state.parts = []; state.wires = []; state.nameCounters = {}; state.nextId = 1;
  addPart('res', 0, 0, 90);          // terminals (-40,0) (40,0)
  pushHistory();
  setTool('WIRE');
  state.cursorInside = true;
  state.cursorWorld = [-40, 0];      // exactly on a terminal
  render();
  const onTerminal = !!document.querySelector('.snap-ring');
  state.cursorWorld = [120, 120];    // empty space
  render();
  const onEmpty = !!document.querySelector('.snap-ring');
  setTool('select');
  return { onTerminal, onEmpty };
});
assert(snap.onTerminal && !snap.onEmpty,
       `snap ring only on valid targets (${JSON.stringify(snap)})`);

// (6) Starter card: shows on empty canvas when not dismissed; Load
// example builds a circuit; dismiss persists.
const card = await page.evaluate(() => {
  localStorage.removeItem('sycan.sedra.welcome.v1');
  state.parts = []; state.wires = []; state.nameCounters = {}; state.nextId = 1;
  pushHistory(); render();
  const shown = !!document.querySelector('.starter-card');
  document.querySelector('.starter-example').click();
  const loaded = state.parts.length > 0 && !document.querySelector('.starter-card');
  // empty again -> card returns; dismiss it
  state.parts = []; state.wires = []; pushHistory(); render();
  document.querySelector('.starter-close').click();
  const dismissed = !document.querySelector('.starter-card')
    && localStorage.getItem('sycan.sedra.welcome.v1') === '1';
  render();
  const staysAway = !document.querySelector('.starter-card');
  return { shown, loaded, dismissed, staysAway };
});
assert(card.shown, 'starter card shows on a fresh empty canvas');
assert(card.loaded, 'Load example builds a circuit and hides the card');
assert(card.dismissed && card.staysAway, 'dismiss persists');

await browser.close();
process.exit(summary(errors));
