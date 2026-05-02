"use strict";

// ==================================================================
// glyphs.ts — schematic component library + glyph rendering
//
// Loaded *before* editor.ts (two `<script defer>` tags in
// index.html, in that order; the compiled JS files keep the same
// shape). This file owns:
//
//   * the coordinate-system constants (GRID, SCALE, SPINE, STEP,
//     NODE_R, HIT_PAD) so the placement grid and the glyph scale
//     can't drift out of sync,
//   * a few low-level SVG helpers (`SVGNS`, `el`, `snap`, `snapPt`)
//     that drawPart and the editor renderer both use,
//   * the `ELEM_TYPES` table — one entry per sycan component class
//     in `src/sycan/components/...` — plus `DEFAULT_VALUES`,
//   * the glyph cache populated at startup by `loadGlyphs()` from
//     `../repl/res/<kind>.svg`,
//   * geometry helpers that consume the cache: `rotateLocal`,
//     `partTerminals`, `partBBox`, `drawPart`,
//   * the cross-file type declarations (Part, Wire, State, etc.) so
//     editor.ts can use them without re-deriving the shapes.
//
// Top-level `const`/`let` declarations in a non-module script live
// in a shared global lexical environment, so editor.ts reads
// `ELEM_TYPES`, `glyphs`, `drawPart`, etc. directly without any
// import boilerplate. The TypeScript compiler is told (via
// `module: "none"` in tsconfig.json) to keep the same model: no
// `import`/`export` keywords appear in either file, so the compiler
// treats both as non-modules and they share the global namespace at
// type-check time too.
// ==================================================================


// ------------------------------------------------------------------
// Cross-file types
//
// Editor state shapes go here so editor.ts can reference them in
// state literals and helper signatures without re-declaring.
// ------------------------------------------------------------------
type Point = [number, number];

type ElemKind =
  | 'res' | 'ind' | 'cap'
  | 'vsrc' | 'isrc'
  | 'diode' | 'npn' | 'pnp'
  | 'nmos' | 'pmos' | 'nmos_4t' | 'pmos_4t' | 'triode'
  | 'vcvs' | 'vccs' | 'cccs' | 'ccvs'
  | 'gnd';

interface PortDef {
  name: string;
  pos: Point;
}

// Resolve a port name to the SPICE node name attached to it. Each
// kind's `netlist` emitter calls this for each port it cares about.
type NodeFn = (portName: string) => string;

interface ElemType {
  glyph: string;
  prefix: string;
  label: string;
  ports: PortDef[];
  // First-port name by default; some kinds override (e.g. ground's
  // single port lives at (0, 0), not at the spine top).
  anchor?: string;
  // Pre-computed `translate(...) scale(...)` for placing the cached
  // glyph fragment so its anchor port lines up with the editor's
  // local origin. Filled in by `loadGlyphs`.
  glyphTransform?: string;
  // Emit a netlist line (or null to skip the part).
  netlist: (p: Part, node: NodeFn) => string | null;
}

interface Part {
  id: string;
  type: ElemKind;
  x: number;
  y: number;
  rot: number;
  value?: string;
  ctrlSrc?: string;   // current-controlled sources only
}

interface Wire {
  id: string;
  points: Point[];
  label?: string;
}

interface Glyph {
  viewBox: number[];
  portsNative: Record<string, Point>;
  inner: string;
}

// Origin info used by move-mode to restore positions on cancel.
//
//   * `wire`           — explicitly-selected wire; translates by the
//                        full delta (current behaviour).
//   * `wire-captured`  — auto-captured by drag-mode because both
//                        endpoints sit on terminals of selected parts;
//                        also translates by delta.
//   * `wire-spanning`  — auto-captured by drag-mode because exactly
//                        one endpoint sits on a selected part's
//                        terminal. The inside endpoint follows the
//                        delta; the outside stays anchored; the path
//                        between them is re-routed each frame.
type MoveOrig =
  | { kind: 'part'; x: number; y: number }
  | { kind: 'wire'; points: Point[] }
  | { kind: 'wire-captured'; points: Point[] }
  | { kind: 'wire-spanning';
      points: Point[];
      insideEnd: 'start' | 'end';
      axisHint: 'h' | 'v'; };

interface MoveDraft {
  ids: string[];
  origs: Map<string, MoveOrig>;
  pickup: Point;
  delta: Point;
  viaDrag: boolean;
  freshlyPasted: boolean;
  // Drag-mode flags captured at startMove time so the user can flip
  // the toolbar checkboxes mid-drag without affecting an in-flight
  // operation.
  dragMode: boolean;
  parityCheck: boolean;
  // Pre-drag connectivity signature (sorted normalised partition of
  // every part-terminal into its DSU root). `null` when parity check
  // is disabled. `commitMove` compares against the post-drag value
  // and reverts the drag if they differ.
  paritySig: string | null;
}

interface BoxSelect {
  x0: number; y0: number;
  x1: number; y1: number;
  additive: boolean;
}

interface WireDraft {
  points: Point[];
  cursor: Point;
  axisFirst: 'h' | 'v';
}

interface CopyAnchorPending {
  selectedIds: string[];
  cut: boolean;
}

interface NetHighlightOverlay {
  node: string;
  gridPoints: Set<string>;
}

// Narrow visual selection on a single wire segment. `selectedIds`
// still holds the parent wire's id so delete/copy/move keep working
// on the whole wire — this only changes what *renders* highlighted.
// Pressing `u` promotes a segment selection to the whole net.
interface SegmentSelection {
  wireId: string;
  segIdx: number;
}

interface CalcNodeHighlight {
  node: string;
  gridPoints: Set<string>;
}

type ToolName =
  | 'select' | 'delete' | 'rotate' | 'highlight'
  | 'WIRE'
  | ElemKind;

interface CalcNodeArm {
  armed: boolean;
  mode: 'auto' | 'dc' | 'ac';
}

interface EditorState {
  parts: Part[];
  wires: Wire[];
  nextId: number;
  nameCounters: Record<string, number>;
  tool: ToolName;
  selectedIds: Set<string>;
  pan: { x: number; y: number };
  zoom: number;
  wireDraft: WireDraft | null;
  boxSelect: BoxSelect | null;
  cursorWorld: Point;
  cursorInside: boolean;
  copyAnchorPending: CopyAnchorPending | null;
  moveDraft: MoveDraft | null;
  calcNode: CalcNodeArm;
  calcNodeHighlight: CalcNodeHighlight | null;
  netHighlightOverlay: NetHighlightOverlay | null;
  selectedSegment: SegmentSelection | null;
}

// Editor's own clipboard payload — *not* the browser `Clipboard`
// global from the DOM lib (which represents `navigator.clipboard`).
interface ClipboardData {
  parts: Array<{
    type: ElemKind;
    dx: number;
    dy: number;
    rot: number;
    value?: string;
    ctrlSrc?: string;
  }>;
  wires: Array<{
    points: Point[];
    label?: string;
  }>;
}

// drawPart options. `hitParent`, when supplied, receives the
// part's invisible hit-test rect (kept off the rotating <g> so
// pointer events use world-space hit boxes).
interface DrawPartOpts {
  preview?: boolean;
  hitParent?: SVGElement;
  selected?: boolean;
  hover?: boolean;
}


// ------------------------------------------------------------------
// Coordinate system
//
// The editor uses a 20-px grid. Every component renders the SVG glyph
// from `res/` at 2× its native scale, which means a glyph spine
// (top→bottom port distance, 40 native units) becomes 80 editor px =
// 4 grid cells. Side-port offsets (npn base 20, nmos gate 30, triode
// cathode offset 10, ccsrc nc± 10/30) all land on multiples of 10
// after scaling, hence the half-grid `STEP` constant we use when
// computing port positions. Every port still snaps cleanly to a 20-px
// grid intersection so wires connect without slop.
// ------------------------------------------------------------------
const GRID = 20;
const SCALE = 2;          // glyph native units → editor px
const SPINE = 80;         // top→bot spine port distance (editor px)
const STEP = 20;          // fundamental snap step
const NODE_R = 3;
const HIT_PAD = 8;        // hit-test slack (px) for clicks on parts/wires


// ------------------------------------------------------------------
// SVG helpers
// ------------------------------------------------------------------
const SVGNS = 'http://www.w3.org/2000/svg';

type AttrValue = string | number | boolean | null | undefined;
type AttrMap = Record<string, AttrValue>;

function el<K extends keyof SVGElementTagNameMap>(
  tag: K, attrs?: AttrMap, parent?: Element | null
): SVGElementTagNameMap[K];
function el(tag: string, attrs?: AttrMap, parent?: Element | null): SVGElement;
function el(tag: string, attrs: AttrMap = {}, parent?: Element | null): SVGElement {
  const e = document.createElementNS(SVGNS, tag) as SVGElement;
  for (const k in attrs) {
    const v = attrs[k];
    if (v === undefined || v === null) continue;
    e.setAttribute(k, String(v));
  }
  if (parent) parent.appendChild(e);
  return e;
}

function snap(v: number): number { return Math.round(v / GRID) * GRID; }
function snapPt(p: Point): Point { return [snap(p[0]), snap(p[1])]; }


// ------------------------------------------------------------------
// Component library — mirrors sycan's component classes
// (`src/sycan/components/...`) and references the same SVG glyphs in
// `res/` (loaded over HTTP at startup; see `loadGlyphs`).
//
// Each entry declares:
//   - `glyph`:    SVG file under res/ (without the .svg extension).
//   - `prefix`:   netlist instance prefix (R, L, C, D, Q, M, ...).
//   - `label`:    human-readable name for menus.
//   - `ports`:    canonical port positions in *post-scale* editor px,
//                 around the part's anchor (0,0). The order is the
//                 SPICE/sycan order so the netlist line writes out
//                 cleanly.
//   - `anchor`:   which port name lives at (0, 0) — the click-to-place
//                 reference. Defaults to the first port for 1-terminal
//                 parts and the spine-top port for everything else.
//   - `netlist`:  emit function that turns a part + node-name lookup
//                 into a netlist line (or null to skip).
// ------------------------------------------------------------------

// Symmetric 2-terminal port table: spine top at (0, -SPINE/2), bot at
// (0, +SPINE/2). Used by R, L, C, V, I, D.
const PORTS_2T = (top: string, bot: string): PortDef[] => [
  { name: top, pos: [0, -SPINE / 2] },
  { name: bot, pos: [0,  SPINE / 2] },
];

// 3-terminal BJT/MOSFET port table. `top`/`bot` swap for PMOS/PNP.
// `gate_dx` is the side-port horizontal offset from the spine (negative
// because the gate/base port lives on the left of the glyph).
const PORTS_3T = (top: string, side: string, bot: string, gate_dx: number): PortDef[] => [
  { name: top,  pos: [0, -SPINE / 2] },
  { name: side, pos: [gate_dx, 0] },
  { name: bot,  pos: [0, SPINE / 2] },
];

const ELEM_TYPES: Record<ElemKind, ElemType> = {
  // ---- Passive (basic/) ----
  res: {
    glyph: 'res',     prefix: 'R',  label: 'Resistor',
    ports: PORTS_2T('n_plus', 'n_minus'),
    netlist: (p, node) =>
      `${p.id} ${node('n_plus')} ${node('n_minus')} ${p.value || p.id}`,
  },
  ind: {
    glyph: 'ind',     prefix: 'L',  label: 'Inductor',
    ports: PORTS_2T('n_plus', 'n_minus'),
    netlist: (p, node) =>
      `${p.id} ${node('n_plus')} ${node('n_minus')} ${p.value || p.id}`,
  },
  cap: {
    glyph: 'cap',     prefix: 'C',  label: 'Capacitor',
    ports: PORTS_2T('n_plus', 'n_minus'),
    netlist: (p, node) =>
      `${p.id} ${node('n_plus')} ${node('n_minus')} ${p.value || p.id}`,
  },

  // ---- Sources (basic/) ----
  vsrc: {
    glyph: 'vsrc',    prefix: 'V',  label: 'Voltage source',
    ports: PORTS_2T('n_plus', 'n_minus'),
    netlist: (p, node) =>
      `${p.id} ${node('n_plus')} ${node('n_minus')} ${p.value || p.id}`,
  },
  isrc: {
    glyph: 'isrc',    prefix: 'I',  label: 'Current source',
    ports: PORTS_2T('n_plus', 'n_minus'),
    netlist: (p, node) =>
      `${p.id} ${node('n_plus')} ${node('n_minus')} ${p.value || p.id}`,
  },

  // ---- Active (active/) ----
  diode: {
    glyph: 'diode',   prefix: 'D',  label: 'Diode',
    ports: PORTS_2T('anode', 'cathode'),
    netlist: (p, node) =>
      `${p.id} ${node('anode')} ${node('cathode')} ${p.value || 'DMOD'}`,
  },
  npn: {
    glyph: 'npn',     prefix: 'Q',  label: 'BJT (NPN)',
    ports: PORTS_3T('collector', 'base', 'emitter', -2 * STEP),
    netlist: (p, node) =>
      `${p.id} ${node('collector')} ${node('base')} ${node('emitter')} ` +
      `${p.value || 'NPN'}`,
  },
  pnp: {
    glyph: 'pnp',     prefix: 'Q',  label: 'BJT (PNP)',
    // PNP swaps collector/emitter on the spine.
    ports: PORTS_3T('emitter', 'base', 'collector', -2 * STEP),
    netlist: (p, node) =>
      `${p.id} ${node('collector')} ${node('base')} ${node('emitter')} ` +
      `${p.value || 'PNP'}`,
  },
  nmos: {
    glyph: 'nmos',    prefix: 'M',  label: 'MOSFET (NMOS)',
    ports: PORTS_3T('drain', 'gate', 'source', -3 * STEP),
    netlist: (p, node) =>
      `${p.id} ${node('drain')} ${node('gate')} ${node('source')} ` +
      `${p.value || 'NMOS'}`,
  },
  pmos: {
    glyph: 'pmos',    prefix: 'M',  label: 'MOSFET (PMOS)',
    ports: PORTS_3T('source', 'gate', 'drain', -3 * STEP),
    netlist: (p, node) =>
      `${p.id} ${node('drain')} ${node('gate')} ${node('source')} ` +
      `${p.value || 'PMOS'}`,
  },
  nmos_4t: {
    glyph: 'nmos_4t', prefix: 'M',  label: 'MOSFET 4-T (NMOS)',
    ports: [
      { name: 'drain',  pos: [0, -SPINE / 2] },
      { name: 'gate',   pos: [-3 * STEP, 0] },
      { name: 'bulk',   pos: [+STEP, 0] },
      { name: 'source', pos: [0, SPINE / 2] },
    ],
    netlist: (p, node) =>
      `${p.id} ${node('drain')} ${node('gate')} ${node('source')} ` +
      `${node('bulk')} ${p.value || 'NMOS'}`,
  },
  pmos_4t: {
    glyph: 'pmos_4t', prefix: 'M',  label: 'MOSFET 4-T (PMOS)',
    ports: [
      { name: 'source', pos: [0, -SPINE / 2] },
      { name: 'gate',   pos: [-3 * STEP, 0] },
      { name: 'bulk',   pos: [+STEP, 0] },
      { name: 'drain',  pos: [0, SPINE / 2] },
    ],
    netlist: (p, node) =>
      `${p.id} ${node('drain')} ${node('gate')} ${node('source')} ` +
      `${node('bulk')} ${p.value || 'PMOS'}`,
  },
  triode: {
    glyph: 'triode',  prefix: 'X',  label: 'Triode',
    ports: [
      { name: 'plate',   pos: [0,        -SPINE / 2] },
      { name: 'grid',    pos: [-2 * STEP, 0] },
      { name: 'cathode', pos: [-STEP,     SPINE / 2] },
    ],
    netlist: (p, node) =>
      `${p.id} ${node('plate')} ${node('grid')} ${node('cathode')} ` +
      `${p.value || 'TRIODE'}`,
  },

  // ---- Controlled sources (basic/) — share ccsrc.svg ----
  vcvs: {
    glyph: 'ccsrc',   prefix: 'E',  label: 'VCVS (E)',
    ports: [
      { name: 'n_plus',   pos: [0,        -SPINE / 2] },
      { name: 'nc_plus',  pos: [-2 * STEP, -STEP] },
      { name: 'nc_minus', pos: [-2 * STEP,  STEP] },
      { name: 'n_minus',  pos: [0,         SPINE / 2] },
    ],
    netlist: (p, node) =>
      `${p.id} ${node('n_plus')} ${node('n_minus')} ` +
      `${node('nc_plus')} ${node('nc_minus')} ${p.value || '1'}`,
  },
  vccs: {
    glyph: 'ccsrc',   prefix: 'G',  label: 'VCCS (G)',
    ports: [
      { name: 'n_plus',   pos: [0,        -SPINE / 2] },
      { name: 'nc_plus',  pos: [-2 * STEP, -STEP] },
      { name: 'nc_minus', pos: [-2 * STEP,  STEP] },
      { name: 'n_minus',  pos: [0,         SPINE / 2] },
    ],
    netlist: (p, node) =>
      `${p.id} ${node('n_plus')} ${node('n_minus')} ` +
      `${node('nc_plus')} ${node('nc_minus')} ${p.value || '1'}`,
  },
  cccs: {
    glyph: 'ccsrc',   prefix: 'F',  label: 'CCCS (F)',
    // Current-controlled: only `ctrl` side port (the controlling V).
    ports: [
      { name: 'n_plus',  pos: [0,        -SPINE / 2] },
      { name: 'ctrl',    pos: [-2 * STEP, 0] },
      { name: 'n_minus', pos: [0,         SPINE / 2] },
    ],
    netlist: (p, node) =>
      `${p.id} ${node('n_plus')} ${node('n_minus')} ` +
      `${p.ctrlSrc || 'V?'} ${p.value || '1'}`,
  },
  ccvs: {
    glyph: 'ccsrc',   prefix: 'H',  label: 'CCVS (H)',
    ports: [
      { name: 'n_plus',  pos: [0,        -SPINE / 2] },
      { name: 'ctrl',    pos: [-2 * STEP, 0] },
      { name: 'n_minus', pos: [0,         SPINE / 2] },
    ],
    netlist: (p, node) =>
      `${p.id} ${node('n_plus')} ${node('n_minus')} ` +
      `${p.ctrlSrc || 'V?'} ${p.value || '1'}`,
  },

  // ---- Connect ----
  gnd: {
    glyph: 'gnd',     prefix: 'GND', label: 'Ground',
    ports: [{ name: 'node', pos: [0, 0] }],
    anchor: 'node',
    netlist: () => null,   // ground does not emit a netlist line
  },
};

// Convenience aliases for the kinds whose port table doesn't list
// `n_plus`/`anode` etc. as the spine-top — `anchor` defaults to the
// first port, but for kinds where the click-to-place reference is the
// spine *centre* (most parts) we let it default to that.
for (const k of Object.keys(ELEM_TYPES) as ElemKind[]) {
  const t = ELEM_TYPES[k];
  if (!t.anchor) t.anchor = t.ports[0].name;
}

// Glyph cache populated by loadGlyphs() at startup.
//   glyphs[kind] = { viewBox: [x, y, w, h],
//                    portsNative: { name: [gx, gy] },
//                    inner: '<path …/><line …/>…' };
const glyphs: Record<string, Glyph> = Object.create(null);
let glyphsReady = false;


// Default symbolic value per kind. For passive parts we use the
// instance name (e.g. R1 → "R1") so the netlist looks like
// `R1 1 2 R1` — convenient for sycan, which then treats the value
// as a symbol. For ground there's no value. For diodes / BJTs /
// MOSFETs we fall back to a model-name placeholder; the user can
// rename it in the props pane.
const DEFAULT_VALUES: Partial<Record<ElemKind, string>> = {
  diode:   'DMOD',
  npn:     'NPN',
  pnp:     'PNP',
  nmos:    'NMOS',  pmos:    'PMOS',
  nmos_4t: 'NMOS',  pmos_4t: 'PMOS',
  triode:  'TRIODE',
  vcvs: '1', vccs: '1', cccs: '1', ccvs: '1',
  gnd:  '',
};


// ------------------------------------------------------------------
// Glyph loading
//
// Each kind references an SVG file under res/ (mirrored at
// docs/repl/res/ for the deployed site). At startup we fetch every
// referenced glyph, parse its viewBox + port markers, and pre-compute
// the affine transform that places the glyph in editor-local space
// such that its anchor port lands at (0, 0) and its spine-top port
// (or the equivalent canonical port) at (0, -SPINE/2).
//
// Drawing a part is then just "embed the glyph's inner SVG inside a
// <g> rotated about (0,0) and translated to (part.x, part.y)" — the
// per-glyph transform pre-aligned to the editor's grid is applied
// inside that <g>.
// ------------------------------------------------------------------
async function loadGlyphs(): Promise<void> {
  const seen = new Set<string>();
  for (const k of Object.keys(ELEM_TYPES) as ElemKind[]) {
    seen.add(ELEM_TYPES[k].glyph);
  }

  const parser = new DOMParser();
  await Promise.all([...seen].map(async (name) => {
    let text: string;
    try {
      const resp = await fetch(`../repl/res/${name}.svg`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      text = await resp.text();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`failed to load glyph "${name}":`, msg);
      return;
    }
    // Normalise hard-coded stroke colours to `currentColor` so the
    // glyph inherits the editor's stroke variable (light/dark mode
    // aware). The author of these glyphs targeted black-on-white
    // print output; in the editor we let CSS drive the colour.
    text = text
      .replace(/stroke:\s*#[0-9a-f]+/gi, 'stroke:currentColor')
      .replace(/stroke="#[0-9a-f]+"/gi, 'stroke="currentColor"');
    const doc = parser.parseFromString(text, 'image/svg+xml');
    const root = doc.documentElement;
    const vb = (root.getAttribute('viewBox') || '0 0 0 0')
      .split(/\s+/).map(Number);

    // Collect port markers (id="port-<name>"; cx/cy in glyph coords).
    const ports: Record<string, Point> = Object.create(null);
    for (const c of doc.querySelectorAll('[id^="port-"]')) {
      const id = (c.getAttribute('id') || '').slice('port-'.length);
      const cx = parseFloat(c.getAttribute('cx') || c.getAttribute('x') || '0');
      const cy = parseFloat(c.getAttribute('cy') || c.getAttribute('y') || '0');
      ports[id] = [cx, cy];
    }

    // Inner SVG content (everything except the <svg> wrapper). We
    // serialise the children individually so the glyph can be inlined
    // into the editor's main <svg> without nesting.
    const inner = [...root.childNodes]
      .map(n => n.nodeType === 1 ? new XMLSerializer().serializeToString(n) : '')
      .join('');

    glyphs[name] = { viewBox: vb, portsNative: ports, inner };
  }));

  // Compute each kind's glyph transform: scale 2× and translate so
  // that the kind's first port (the anchor) lines up with (0, 0) of
  // its editor-space port table.
  for (const k of Object.keys(ELEM_TYPES) as ElemKind[]) {
    const t = ELEM_TYPES[k];
    const g = glyphs[t.glyph];
    if (!g) continue;
    const anchorName = t.anchor!;
    const anchorNative = g.portsNative[anchorName];
    if (!anchorNative) {
      console.warn(`glyph "${t.glyph}" missing port-${anchorName}; skipped`);
      continue;
    }
    const anchorEditorPort = t.ports.find(p => p.name === anchorName);
    if (!anchorEditorPort) continue;
    const anchorEditor = anchorEditorPort.pos;
    // editor = native * SCALE + shift  ⇒  shift = editor - native*SCALE
    const tx = anchorEditor[0] - SCALE * anchorNative[0];
    const ty = anchorEditor[1] - SCALE * anchorNative[1];
    t.glyphTransform = `translate(${tx},${ty}) scale(${SCALE})`;
  }

  glyphsReady = true;
}

// Pick an anchor point + axis along which to render a wire's net label
// (or any annotation that wants to "ride" the wire). We choose the
// midpoint of the wire's longest axis-aligned segment, with the label
// kept upright (so vertical segments still get horizontal text — placed
// to the right of the segment rather than rotated).
//
// Returns: { x, y, axis } where axis ∈ {'h','v'} reflects the segment
// direction, or null if the wire has no segments.
interface WireLabelAnchor { x: number; y: number; axis: 'h' | 'v'; }

function wireLabelAnchor(wire: Wire | null | undefined): WireLabelAnchor | null {
  if (!wire || !wire.points || wire.points.length < 2) return null;
  let bestLen = -1;
  let bestSeg: [Point, Point] | null = null;
  for (let i = 1; i < wire.points.length; i++) {
    const a = wire.points[i - 1], b = wire.points[i];
    const len = Math.abs(b[0] - a[0]) + Math.abs(b[1] - a[1]);
    if (len > bestLen) { bestLen = len; bestSeg = [a, b]; }
  }
  if (!bestSeg) return null;
  const [a, b] = bestSeg;
  return {
    x: (a[0] + b[0]) / 2,
    y: (a[1] + b[1]) / 2,
    axis: a[1] === b[1] ? 'h' : 'v',
  };
}


function rotateLocal([lx, ly]: Point, rot: number, cx: number, cy: number): Point {
  let x: number, y: number;
  switch (((rot % 360) + 360) % 360) {
    case 90:  x = -ly; y =  lx; break;
    case 180: x = -lx; y = -ly; break;
    case 270: x =  ly; y = -lx; break;
    default:  x =  lx; y =  ly; break;
  }
  return [cx + x, cy + y];
}

interface PartTerminal { name: string; pos: Point; }

// World-space terminal positions for a part — port table looked up by
// kind, then rotated/translated by part.rot and (part.x, part.y).
function partTerminals(p: Part): PartTerminal[] {
  const t = ELEM_TYPES[p.type];
  if (!t) return [];
  return t.ports.map(port => ({
    name: port.name,
    pos: rotateLocal(port.pos, p.rot, p.x, p.y),
  }));
}

// World-space bounding box. We union the glyph viewBox (mapped to
// editor coords by the glyph transform) with every port position so
// pin leads sticking out of the body still sit inside the bbox for
// hit-testing.
function partBBox(p: Part): [number, number, number, number] {
  const t = ELEM_TYPES[p.type];
  if (!t) return [p.x - 20, p.y - 20, p.x + 20, p.y + 20];

  // Local bbox first (pre-rotation).
  let l = Infinity, top = Infinity, r = -Infinity, b = -Infinity;
  for (const port of t.ports) {
    const [px, py] = port.pos;
    l = Math.min(l, px); top = Math.min(top, py);
    r = Math.max(r, px); b = Math.max(b, py);
  }
  const g = glyphs[t.glyph];
  if (g) {
    // Map the glyph viewBox corners through the glyph transform.
    // Transform is `translate(tx,ty) scale(SCALE)`; tx/ty are stored
    // implicitly via t.glyphTransform — recompute from anchor port.
    const anchorName = t.anchor!;
    const anchorNative = g.portsNative[anchorName];
    const anchorEditorPort = t.ports.find(pp => pp.name === anchorName);
    if (anchorNative && anchorEditorPort) {
      const anchorEditor = anchorEditorPort.pos;
      const tx = anchorEditor[0] - SCALE * anchorNative[0];
      const ty = anchorEditor[1] - SCALE * anchorNative[1];
      const [vx, vy, vw, vh] = g.viewBox;
      const corners: Point[] = [
        [vx, vy], [vx + vw, vy], [vx, vy + vh], [vx + vw, vy + vh],
      ];
      for (const [cx, cy] of corners) {
        const ex = tx + SCALE * cx, ey = ty + SCALE * cy;
        l = Math.min(l, ex); top = Math.min(top, ey);
        r = Math.max(r, ex); b = Math.max(b, ey);
      }
    }
  }
  // Slack for hit-testing.
  l -= 2; r += 2; top -= 2; b += 2;

  // Now rotate corners and pick world extrema.
  const corners: Point[] = [[l, top], [r, top], [r, b], [l, b]];
  let xmin = Infinity, ymin = Infinity, xmax = -Infinity, ymax = -Infinity;
  for (const c of corners) {
    const [wx, wy] = rotateLocal(c, p.rot, p.x, p.y);
    xmin = Math.min(xmin, wx); ymin = Math.min(ymin, wy);
    xmax = Math.max(xmax, wx); ymax = Math.max(ymax, wy);
  }
  return [xmin, ymin, xmax, ymax];
}


// `svg` (the canvas <svg> element) is created in editor.ts. We refer
// to it by lexical name here for the selection/hover rectangle
// fallback in `drawPart`. Because both files compile as non-modules
// sharing one global scope, TypeScript resolves the reference into
// editor.ts's `var svg` declaration without an explicit `declare`.

// Build the visual for a single part as an SVG <g>. The glyph is
// inlined verbatim from the cached res/* SVG, transformed into the
// editor's coordinate system by the kind's pre-computed glyph
// transform. The outer <g> rotates the whole thing around the part's
// anchor, so we never have to deal with mid-rotation coordinates.
function drawPart(p: Part, opts: DrawPartOpts = {}): SVGGElement {
  const t = ELEM_TYPES[p.type];
  const g = el('g', {
    transform: `translate(${p.x},${p.y}) rotate(${p.rot})`,
    'data-id': p.id,
  }) as SVGGElement;

  // 1. Glyph body. Two paths to embed inline SVG: parse the cached
  //    string into nodes (correct namespace handling) or use a wrapper
  //    <g> with inner-html setting. We pre-parsed at load, so we just
  //    serialise into innerHTML inside a fresh <g> with the transform.
  const cached = glyphs[t.glyph];
  if (cached && t.glyphTransform) {
    const wrap = el('g', {
      transform: t.glyphTransform,
      class: opts.preview ? 'glyph-preview' : 'glyph',
    }, g) as SVGGElement;
    // Parse the glyph fragment into proper SVG nodes (re-used across
    // every part draw — cheap because it's the same source string).
    const tmpl = `<svg xmlns="${SVGNS}">${cached.inner}</svg>`;
    const tmp = new DOMParser().parseFromString(tmpl, 'image/svg+xml');
    for (const n of [...tmp.documentElement.childNodes]) {
      // Skip the magenta debug rect that lives in some glyphs (it has
      // display:none anyway, but importing dead nodes inflates the DOM).
      if (n.nodeType === 1) {
        const el = n as Element;
        if (el.tagName === 'rect'
            && (el.getAttribute('style') || '').includes('display:none')) {
          continue;
        }
      }
      wrap.appendChild(document.importNode(n, true));
    }
  }

  // 2. Terminal dots — one per port.
  if (!opts.preview) {
    for (const port of t.ports) {
      el('circle', {
        cx: port.pos[0], cy: port.pos[1],
        r: NODE_R, class: 'terminal',
      }, g);
    }
  }

  // 3. Labels (name above, value below) anchored just past the *world-
  //    right* edge of the rotated body, centred vertically around the
  //    rotated body's mid-line. Rendered straight onto `svg` (or
  //    `hitParent` when present) — *not* into the rotating part-<g> —
  //    so the two-line stack stays a clean vertical stack regardless
  //    of the part's rotation. (Keeping the labels inside the part-<g>
  //    used to scatter the value to the opposite side of the name when
  //    the part rotated.) `partBBox` already returns the rotated bbox,
  //    so we just take its right edge plus a small gap.
  if (!opts.preview && p.id) {
    const [bx0, by0, bx1, by1] = partBBox(p);
    const target = opts.hitParent || svg;
    const labelX = bx1 + 6;
    const midY = (by0 + by1) / 2;
    const hasValue = !!p.value && p.value !== p.id && p.type !== 'gnd';
    const nameY = hasValue ? midY - 6 : midY;
    el('text', {
      x: labelX, y: nameY,
      class: 'part-name',
      'text-anchor': 'start',
      'dominant-baseline': 'middle',
    }, target).textContent = p.id;
    if (hasValue) {
      el('text', {
        x: labelX, y: nameY + 12,
        class: 'part-text',
        'text-anchor': 'start',
        'dominant-baseline': 'middle',
      }, target).textContent = p.value!;
    }
  }

  // Invisible bounding hit-rect (for click selection)
  if (!opts.preview && opts.hitParent) {
    const [x0, y0, x1, y1] = partBBox(p);
    // Place in *world coords*, so we don't double-rotate. Detach from g
    // and put it into the parent <g> with no transform.
    el('rect', {
      x: x0, y: y0, width: x1 - x0, height: y1 - y0,
      class: 'hit-rect', 'data-id': p.id, 'data-kind': 'part',
    }, opts.hitParent);
  }

  // Selection / hover decorations
  if (opts.selected || opts.hover) {
    const [x0, y0, x1, y1] = partBBox(p);
    // Drawn into hitParent if available, else into svg (won't rotate
    // correctly but better than nothing).
    const target = opts.hitParent || svg;
    el('rect', {
      x: x0 - 2, y: y0 - 2,
      width: x1 - x0 + 4, height: y1 - y0 + 4,
      class: opts.selected ? 'selection-box' : 'hover-box',
    }, target);
  }

  return g;
}
