// Device-parameter emit + round-trip.
//
// The SPICE parser in sycan requires a positional parameter tail
// after the model name on D / Q / M / triode lines (see
// src/sycan/spice.py). Sedra used to emit the model name only, which
// fell short of the parser's _require(...) length checks. This test
// covers:
//
//   1. Each newly-placed device emits a netlist line whose token
//      count meets the parser's minimum.
//   2. The default model name matches a parser-recognised model
//      (NMOS_L1, PMOS_L1, NMOS_4T, PMOS_4T, NPN, PNP, TRIODE, DMOD).
//   3. User-supplied params override the per-instance symbolic
//      default.
//   4. Params round-trip through pushHistory + JSON snapshot and
//      through the cut/paste clipboard.
//
// The test stays browser-side: it builds the schematic via the
// global helpers (`addPart`, `buildNetlist`, `pushHistory`, `state`)
// and reads the emitted netlist text. We do NOT try to run pyodide
// here — the parser is exercised separately in
// tests/spice/test_sedra_emit.py.

import { setup, assert, assertEqual, summary } from './_helpers.mjs';

const { browser, page, errors } = await setup();

// ---------- helpers run inside the page ----------------------------

// Reset the editor state and place the listed parts at well-spaced
// world coordinates so terminals don't accidentally union into the
// same node. Returns the netlist text that buildNetlist() emits.
async function placeAndEmit(specs) {
  return await page.evaluate((specs) => {
    state.parts = []; state.wires = [];
    state.nameCounters = {}; state.nextId = 1;
    state.selectedIds.clear(); state.selectedSegments.clear();
    let x = 200;
    for (const s of specs) {
      addPart(s.kind, x, 200, 0);
      const fresh = state.parts[state.parts.length - 1];
      if (s.value !== undefined) fresh.value = s.value;
      if (s.params !== undefined) fresh.params = s.params;
      x += 200;
    }
    pushHistory(); render();
    return buildNetlist().text;
  }, specs);
}

// Strip header/comment/.end lines and return the device lines only.
function deviceLines(text) {
  return text.split('\n')
             .map(s => s.trim())
             .filter(s => s && !s.startsWith('*') && s !== '.end');
}

// Return [model, ...tail] for a device line, stripping the prefix +
// node-name tokens. `nodeCount` is the number of node tokens between
// the instance name and the model: 2 for two-terminal devices, 3 for
// 3-terminal, 4 for 4-T MOSFETs and triode-as-X.
function tail(line, nodeCount) {
  const tokens = line.split(/\s+/);
  return tokens.slice(1 + nodeCount);
}

// ---------- (1) every kind reaches the parser's minimum length ----

const MIN_TOKENS = {
  // (sycan/spice.py:_require argument)
  diode:   4,   // D name a c IS
  npn:     8,   // Q name c b e MODEL IS BF BR
  pnp:     8,
  nmos:    10,  // M name d g s MODEL mu Cox W L V_TH
  pmos:    10,
  nmos_4t: 11,  // M name d g s b MODEL mu Cox W L V_TH0
  pmos_4t: 11,
  triode:  7,   // X name p g k TRIODE K mu
};

const text = await placeAndEmit([
  { kind: 'diode' },
  { kind: 'npn' },
  { kind: 'pnp' },
  { kind: 'nmos' },
  { kind: 'pmos' },
  { kind: 'nmos_4t' },
  { kind: 'pmos_4t' },
  { kind: 'triode' },
]);
const lines = deviceLines(text);

// The kinds we placed in placement order; SPICE-prefix sort means the
// emit order is V/I/R/L/C/D/Q/M/X — for our placements that's:
//   D1 (diode), Q1 (npn), Q2 (pnp), M1..M4 (nmos, pmos, nmos_4t, pmos_4t),
//   X1 (triode).
const expectedKinds = ['diode', 'npn', 'pnp', 'nmos', 'pmos',
                       'nmos_4t', 'pmos_4t', 'triode'];

assertEqual(lines.length, expectedKinds.length,
            'one netlist line emitted per device');
for (let i = 0; i < expectedKinds.length; i++) {
  const kind = expectedKinds[i];
  const line = lines[i];
  const tokens = line.split(/\s+/);
  assert(tokens.length >= MIN_TOKENS[kind],
         `${kind}: line has ≥ ${MIN_TOKENS[kind]} tokens ` +
         `(got ${tokens.length}: "${line}")`);
}

// ---------- (2) default model names match the parser ----------

// Per-kind expected (model token, position from the instance name).
// Position counts the model token as offset = 1 + nodeCount.
const expectedModel = {
  diode:    { model: 'DMOD',    nodeCount: 2 },
  npn:      { model: 'NPN',     nodeCount: 3 },
  pnp:      { model: 'PNP',     nodeCount: 3 },
  nmos:     { model: 'NMOS_L1', nodeCount: 3 },
  pmos:     { model: 'PMOS_L1', nodeCount: 3 },
  nmos_4t:  { model: 'NMOS_4T', nodeCount: 4 },
  pmos_4t:  { model: 'PMOS_4T', nodeCount: 4 },
  triode:   { model: 'TRIODE',  nodeCount: 3 },
};

for (let i = 0; i < expectedKinds.length; i++) {
  const kind = expectedKinds[i];
  const exp = expectedModel[kind];
  const t = tail(lines[i], exp.nodeCount);
  assertEqual(t[0], exp.model,
              `${kind}: model token defaults to "${exp.model}"`);
}

// ---------- (3) symbolic-default params per instance --------------

// Q1 (NPN) should default to "Q1_IS Q1_BF Q1_BR".
const q1Tail = tail(lines[1], 3);  // skip model + 3 nodes
assertEqual(q1Tail.slice(1), ['Q1_IS', 'Q1_BF', 'Q1_BR'],
            'NPN: default params are <id>_IS <id>_BF <id>_BR');

// M1 (NMOS_L1) → "M1_mu M1_Cox M1_W M1_L M1_VTH"
const m1Tail = tail(lines[3], 3);
assertEqual(m1Tail.slice(1),
            ['M1_mu', 'M1_Cox', 'M1_W', 'M1_L', 'M1_VTH'],
            'NMOS_L1: default params are <id>_mu <id>_Cox <id>_W <id>_L <id>_VTH');

// M3 (NMOS_4T) → same five params, after the bulk node.
const m3Tail = tail(lines[5], 4);
assertEqual(m3Tail.slice(1),
            ['M3_mu', 'M3_Cox', 'M3_W', 'M3_L', 'M3_VTH'],
            'NMOS_4T: default params follow the bulk node');

// Triode → "X1_K X1_mu"
const x1Tail = tail(lines[7], 3);
assertEqual(x1Tail.slice(1), ['X1_K', 'X1_mu'],
            'Triode: default params are <id>_K <id>_mu');

// ---------- (4) user-supplied params override the default ---------

const userText = await placeAndEmit([
  { kind: 'npn', params: '1e-15 100 1 0.026' },        // IS BF BR V_T
  { kind: 'nmos', value: 'NMOS_3T', params: '0.05 1e-9 1u 0.5u 0.7 0.02' },
]);
const userLines = deviceLines(userText);
assertEqual(userLines.length, 2, 'user-params test emits one line per device');

const qTokens = userLines[0].split(/\s+/);
assertEqual(qTokens.slice(-4), ['1e-15', '100', '1', '0.026'],
            'NPN: user-supplied params replace the symbolic default');

const mTokens = userLines[1].split(/\s+/);
assertEqual(mTokens[4], 'NMOS_3T',
            'NMOS: user-edited model token is emitted verbatim');
assertEqual(mTokens.slice(5), ['0.05', '1e-9', '1u', '0.5u', '0.7', '0.02'],
            'NMOS_3T: 6 params (5 base + lam) appear after the model');

// ---------- (5) round-trip through history snapshots --------------

const roundTrip = await page.evaluate(() => {
  state.parts = []; state.wires = [];
  state.nameCounters = {}; state.nextId = 1;
  state.selectedIds.clear(); state.selectedSegments.clear();
  addPart('npn', 200, 200, 0);
  const q = state.parts[state.parts.length - 1];
  q.params = '2e-16 250 0.5';
  pushHistory();
  // Mutate, then undo — the params field should ride the snapshot.
  q.params = 'broken';
  pushHistory();
  // pushHistory captures the broken snapshot; restoring the
  // previous snapshot (one step back in editHistory) brings back
  // the params we set first.
  restore(historyIdx - 1);
  return state.parts[0].params;
});
assertEqual(roundTrip, '2e-16 250 0.5',
            'params round-trip through pushHistory + undo');

// ---------- (6) clipboard cut/paste preserves params --------------

const pasted = await page.evaluate(() => {
  state.parts = []; state.wires = [];
  state.nameCounters = {}; state.nextId = 1;
  state.selectedIds.clear(); state.selectedSegments.clear();
  addPart('nmos', 200, 200, 0);
  const m = state.parts[state.parts.length - 1];
  m.params = '0.05 1e-9 2u 0.5u 0.6';
  m.value = 'NMOS_L1';
  pushHistory();
  // Select + copy + paste at a different anchor.
  state.selectedIds.add(m.id);
  state.cursorWorld = [400, 200];  // paste anchor
  copySelection(false);
  finalizeCopyAnchor([200, 200]);  // anchor for the source set
  pasteClipboard();
  // Now there are two NMOS parts; the second should carry params
  // verbatim (its id will differ).
  const ms = state.parts.filter(p => p.type === 'nmos');
  return ms.map(p => ({ id: p.id, value: p.value, params: p.params }));
});
assert(pasted.length === 2, 'paste produced a second nmos instance');
assertEqual(pasted[1].value, 'NMOS_L1',
            'paste preserves the model name');
assertEqual(pasted[1].params, '0.05 1e-9 2u 0.5u 0.6',
            'paste preserves the params string');

await browser.close();
process.exit(summary(errors));
