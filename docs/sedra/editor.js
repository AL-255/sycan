"use strict";

// ==================================================================
// editor.js — interactive shell of the schematic editor
//
// State + history, mouse/keyboard/clipboard handling, the
// renderer, netlist generator, and persistence. Component
// definitions and glyph drawing live in glyphs.js (loaded first).
// ==================================================================


// ------------------------------------------------------------------
// State — single source of truth, serialised to localStorage.
// ------------------------------------------------------------------
const state = {
  parts: [],            // {id, type, x, y, rot, value}
  wires: [],            // {id, points: [[x,y],...]}
  nextId: 1,            // monotonic counter for unique wire ids
  nameCounters: {},     // per-prefix counter, e.g. {R: 3, C: 1}
  tool: 'res',
  // Multi-selection: a Set of part ids. (Wires aren't selectable for
  // copy/paste yet — they're easy to redraw.)
  selectedIds: new Set(),
  pan: { x: 0, y: 0 },  // screenX = pan.x + worldX * zoom
  zoom: 1,
  // Manhattan wire scratchpad: { points: [[x,y],...], cursor, axisFirst }
  // - `points` holds every committed corner of the wire-in-progress.
  //   The first entry is the starting click; each click extends with
  //   one or two grid-aligned segments.
  // - `cursor` is the live snapped cursor position (for preview only).
  // - `axisFirst` ∈ {'h','v'} hints which axis the next L starts with;
  //   recomputed each move from the cursor delta.
  wireDraft: null,
  // Box-select drag state (only active in 'select' tool).
  boxSelect: null,      // {x0, y0, x1, y1} in world coords during drag
  // Last cursor position in world coords (for paste anchoring).
  cursorWorld: [0, 0],
  // Whether the cursor is currently over the canvas — gates the
  // grid-snap crosshair indicator drawn by render(). Toggled by
  // the mousemove / mouseleave handlers.
  cursorInside: false,
  // Pending copy/cut: after Ctrl+C/X we don't capture the clipboard
  // immediately — instead we wait for the user to click a canvas
  // point that becomes the anchor. The clipboard then stores every
  // selected item's position as an offset from that anchor, so on
  // paste the anchor lands at the cursor. `Esc` cancels.
  //   { selectedIds: [...], cut: boolean }
  copyAnchorPending: null,
  // Active move operation. While set, every render places parts and
  // wires at their snapshotted-origin position plus `delta`. Cleared
  // by `commitMove` (locks in the new positions and pushes history)
  // or `cancelMove` (restores originals; if `freshlyPasted`, deletes
  // the items entirely).
  //   {
  //     ids: string[],                  // every selected id
  //     origs: Map<id, {kind:'part', x, y} | {kind:'wire', points}>,
  //     pickup: [x, y],                 // world point delta is measured from
  //     delta:  [dx, dy],               // current snapped offset
  //     viaDrag: boolean,               // true → commit on mouseup;
  //                                     // false → commit on next click
  //     freshlyPasted: boolean,         // true → cancel removes the items
  //   }
  moveDraft: null,
};

// Clipboard. Holds both parts and wires; positions are offsets from
// the bbox top-left of the copied set.
let clipboard = { parts: [], wires: [] };

// History for undo/redo (snapshots of {parts, wires, nameCounters}).
const history = [];
let historyIdx = -1;
const HISTORY_LIMIT = 100;


// ------------------------------------------------------------------
// DOM refs
// ------------------------------------------------------------------
const svg = document.getElementById('canvas');
const wrap = document.getElementById('canvas-wrap');
const hint = document.getElementById('hint');
const propPane = document.getElementById('prop-pane');
const netlistEl = document.getElementById('netlist');

// Convert a screen-space mouse event to world (logical) coordinates.
function eventToWorld(e) {
  const rect = wrap.getBoundingClientRect();
  const sx = e.clientX - rect.left;
  const sy = e.clientY - rect.top;
  return [
    (sx - state.pan.x) / state.zoom,
    (sy - state.pan.y) / state.zoom,
  ];
}

// ------------------------------------------------------------------
// Wire drawing
// ------------------------------------------------------------------

// Build a wire path "M x,y L x,y ...".
function wirePath(points) {
  if (!points.length) return '';
  let d = `M${points[0][0]},${points[0][1]}`;
  for (let i = 1; i < points.length; i++) {
    d += ` L${points[i][0]},${points[i][1]}`;
  }
  return d;
}

// Build the L-segment from `from` to `to` honouring the requested
// `axisFirst` axis ('h' or 'v'). Returns the list of intermediate
// vertices (0 if axis-aligned, 1 corner otherwise).
function lSegment(from, to, axisFirst) {
  if (from[0] === to[0] || from[1] === to[1]) return [];
  return axisFirst === 'h'
    ? [[to[0], from[1]]]   // horizontal first → corner shares to.x, from.y
    : [[from[0], to[1]]];  // vertical first   → corner shares from.x, to.y
}

// ------------------------------------------------------------------
// Render — full redraw on every state change. Cheap enough for the
// schematic sizes we care about, and trivially correct.
// ------------------------------------------------------------------
function render() {
  // Sync canvas viewBox to current pan/zoom.
  const W = wrap.clientWidth, H = wrap.clientHeight;
  svg.setAttribute('width', W);
  svg.setAttribute('height', H);
  svg.setAttribute('viewBox',
    `${-state.pan.x / state.zoom} ${-state.pan.y / state.zoom} ` +
    `${W / state.zoom} ${H / state.zoom}`);

  // Clear and rebuild
  while (svg.firstChild) svg.removeChild(svg.firstChild);

  // Layer 0: grid (drawn within the current viewBox)
  drawGrid();

  // Layer 1: wires
  for (const w of state.wires) {
    const sel = state.selectedIds.has(w.id);
    el('path', { d: wirePath(w.points),
                 class: sel ? 'wire wire-selected' : 'wire',
                 'data-id': w.id, 'data-kind': 'wire' }, svg);
    // Invisible thicker hit-stroke for easier clicking
    el('path', { d: wirePath(w.points), class: 'hit',
                 'data-id': w.id, 'data-kind': 'wire' }, svg);
  }

  // Layer 2: parts
  const partsLayer = el('g', { id: 'parts-layer' }, svg);
  const hitLayer = el('g', { id: 'hit-layer' }, svg);
  for (const p of state.parts) {
    const g = drawPart(p, {
      hitParent: hitLayer,
      selected: state.selectedIds.has(p.id),
    });
    partsLayer.appendChild(g);
  }

  // Layer 3: node dots at junctions where >=3 endpoints meet
  drawJunctions();

  // Layer 3.5: live box-select rectangle
  if (state.boxSelect) {
    const b = state.boxSelect;
    const x = Math.min(b.x0, b.x1), y = Math.min(b.y0, b.y1);
    const w = Math.abs(b.x1 - b.x0), h = Math.abs(b.y1 - b.y0);
    el('rect', {
      x, y, width: w, height: h,
      fill: 'rgba(25, 118, 210, 0.08)',
      stroke: 'var(--select)', 'stroke-width': 1.2,
      'stroke-dasharray': '4 3',
    }, svg);
  }

  // Layer 4: wire-drawing preview
  if (state.wireDraft) {
    drawWirePreview();
  }

  // Layer 5: tool placement preview
  if (placementPreview) {
    const previewG = drawPart(placementPreview, { preview: true });
    svg.appendChild(previewG);
  }

  // Layer 6: grid-snap crosshair at the cursor. Always-on while the
  // cursor is over the canvas so the user can see exactly which grid
  // intersection the next click will land on. The cross-hairs span
  // the full visible viewport (KiCad-style) so the user can sight
  // the cursor against any other point in the schematic. Styling
  // steps up during anchor-pick (a small ring at the centre +
  // brighter strokes) since picking the anchor is the explicit
  // purpose of that mode.
  if (state.cursorInside) {
    const [cx, cy] = snapPt(state.cursorWorld);
    const armed = !!state.copyAnchorPending;
    // Visible world-space rectangle, matching the SVG viewBox we set
    // at the top of render(). The `+1` slack hides any sub-pixel
    // edge that the browser might paint at extreme zoom.
    const viewX = -state.pan.x / state.zoom;
    const viewY = -state.pan.y / state.zoom;
    const viewW = wrap.clientWidth  / state.zoom;
    const viewH = wrap.clientHeight / state.zoom;
    // Amber crosshair (#ffb405) — distinct from the blue selection
    // accent so it never gets confused with a selected wire.
    const crossColor = '#ffb405';
    el('path', {
      d: `M${viewX - 1},${cy} h${viewW + 2} ` +
         `M${cx},${viewY - 1} v${viewH + 2}`,
      stroke: crossColor,
      // `vector-effect: non-scaling-stroke` keeps the stroke width
      // measured in *screen* pixels regardless of the current zoom,
      // so the cross-hairs never shrink below 1 px when the user
      // zooms out.
      'stroke-width': armed ? 1.5 : 1,
      'vector-effect': 'non-scaling-stroke',
      fill: 'none',
      opacity: armed ? '0.85' : '0.55',
      'pointer-events': 'none',
    }, svg);
    if (armed) {
      el('circle', {
        cx, cy, r: 4 / state.zoom, fill: 'none',
        stroke: crossColor, 'stroke-width': 1.4,
        'vector-effect': 'non-scaling-stroke',
      }, svg);
    }
  }

  updateNetlist();
  saveLocal();
}

function drawGrid() {
  const W = wrap.clientWidth, H = wrap.clientHeight;
  const x0 = -state.pan.x / state.zoom;
  const y0 = -state.pan.y / state.zoom;
  const x1 = x0 + W / state.zoom;
  const y1 = y0 + H / state.zoom;
  const gx0 = Math.floor(x0 / GRID) * GRID;
  const gy0 = Math.floor(y0 / GRID) * GRID;
  // Use a single <path> of small "+" marks for crisp dots at any zoom.
  let d = '';
  // Cap density at ridiculous zoom-outs.
  if ((x1 - x0) / GRID > 600) return;
  for (let y = gy0; y <= y1; y += GRID) {
    for (let x = gx0; x <= x1; x += GRID) {
      d += `M${x - 1},${y} h2 M${x},${y - 1} v2 `;
    }
  }
  el('path', {
    d,
    fill: 'none',
    stroke: 'var(--grid)',
    'stroke-width': 1,
    'stroke-linecap': 'butt',
  }, svg);
}

function drawJunctions() {
  // Count *incident segments* at each grid point and draw a node dot
  // wherever ≥3 segments meet (a true T or 4-way cross). Each part
  // terminal contributes 1 (the lead). Each wire endpoint contributes
  // 1 (one segment touches it). Each *interior* wire vertex contributes
  // 2 — the two segments on either side. With Steiner T coalescing,
  // wires that branch at a point have one wire continuing through it
  // (interior vertex, contributes 2) and one wire ending there
  // (endpoint, contributes 1) → total 3 → dot. Without coalescing,
  // a wire endpoint that lands mid-segment of another wire would only
  // count as 1+0 = 1 and miss the dot, which is why the coalesce pass
  // exists.
  const counts = new Map();
  const key = ([x, y]) => `${x},${y}`;
  const bump = (pt, n) => {
    const k = key(pt);
    counts.set(k, (counts.get(k) || 0) + n);
  };
  for (const p of state.parts) {
    for (const t of partTerminals(p)) bump(t.pos, 1);
  }
  for (const w of state.wires) {
    if (w.points.length < 2) continue;
    bump(w.points[0], 1);
    bump(w.points[w.points.length - 1], 1);
    for (let i = 1; i < w.points.length - 1; i++) bump(w.points[i], 2);
  }
  for (const [k, count] of counts) {
    if (count < 3) continue;
    const [x, y] = k.split(',').map(Number);
    el('circle', { cx: x, cy: y, r: 3.5, class: 'node-dot' }, svg);
  }
}

// ------------------------------------------------------------------
// Steiner T-junction coalescing
//
// A schematic is "canonical" when every wire passing through another
// wire's vertex (or a part terminal) has that point as a vertex of
// its own polyline too. In other words: shared connection points are
// always represented as shared vertices, never as "this wire happens
// to lie on top of that one".
//
// `coalesceJunctions` drives every wire toward this form. It scans
// each wire's segments and inserts any external "interest point" that
// strictly lies inside the segment as a new vertex (splitting the
// segment in two). Repeats until stable — one insertion can create a
// new junction that triggers another insertion elsewhere.
//
// This is what makes T-joints "automatic": draw a wire whose endpoint
// lands mid-segment on an existing trunk, and the trunk gets split at
// that point so the dot-counting logic in drawJunctions sees three
// incident segments and renders the dot. The netlist's union-find
// continues to work either way (it merges by coordinate equality), so
// coalescing is purely about visual correctness.
// ------------------------------------------------------------------
function pointOnSegment(p, a, b) {
  // Strict interior, axis-aligned segments only (we only ever build
  // those, since `lSegment` snaps to the grid).
  if (a[0] === b[0]) {
    return p[0] === a[0]
      && p[1] > Math.min(a[1], b[1])
      && p[1] < Math.max(a[1], b[1]);
  }
  if (a[1] === b[1]) {
    return p[1] === a[1]
      && p[0] > Math.min(a[0], b[0])
      && p[0] < Math.max(a[0], b[0]);
  }
  return false;
}

function coalesceJunctions() {
  const sameP = (a, b) => a[0] === b[0] && a[1] === b[1];

  // Up to a small fixed number of passes — each pass is O(W·V·S);
  // schematics with hundreds of components fit within the bound and
  // converge in 1–2 passes in practice.
  for (let iter = 0; iter < 8; iter++) {
    // Snapshot every point that *can* trigger a split: part terminals
    // plus every existing wire vertex. Recomputed each pass because
    // splits create new vertices that may themselves T-join.
    const points = [];
    for (const p of state.parts) {
      for (const t of partTerminals(p)) points.push(t.pos);
    }
    for (const w of state.wires) {
      for (const pt of w.points) points.push(pt);
    }

    let changed = false;

    for (const w of state.wires) {
      const out = [w.points[0]];
      for (let i = 1; i < w.points.length; i++) {
        const a = w.points[i - 1];
        const b = w.points[i];
        // Collect every interest-point strictly inside (a,b),
        // ordered along the segment.
        const interior = [];
        for (const p of points) {
          if (sameP(p, a) || sameP(p, b)) continue;
          if (!pointOnSegment(p, a, b)) continue;
          // Distance from `a` along the segment is just |dx|+|dy|
          // for axis-aligned cases.
          const d = Math.abs(p[0] - a[0]) + Math.abs(p[1] - a[1]);
          interior.push({ p, d });
        }
        interior.sort((x, y) => x.d - y.d);
        let prev = a;
        for (const { p } of interior) {
          if (sameP(p, prev)) continue;
          out.push(p);
          prev = p;
          changed = true;
        }
        out.push(b);
      }
      // Drop consecutive duplicates that an interior split could
      // briefly produce when an existing vertex coincides with `b`.
      const dedup = [out[0]];
      for (let i = 1; i < out.length; i++) {
        if (!sameP(out[i], dedup[dedup.length - 1])) dedup.push(out[i]);
      }
      if (dedup.length !== w.points.length) w.points = dedup;
    }

    if (!changed) return;
  }
}

function drawWirePreview() {
  const wd = state.wireDraft;
  if (!wd) return;
  const pts = wd.points.slice();
  if (wd.cursor) {
    const last = pts[pts.length - 1];
    if (wd.cursor[0] !== last[0] || wd.cursor[1] !== last[1]) {
      const corners = lSegment(last, wd.cursor, wd.axisFirst || 'h');
      pts.push(...corners, wd.cursor);
    }
  }
  el('path', { d: wirePath(pts), class: 'preview' }, svg);
  // Highlight already-committed corners.
  for (const pt of wd.points) {
    el('circle', { cx: pt[0], cy: pt[1], r: 3, class: 'node-dot' }, svg);
  }
}

let placementPreview = null;  // ghost part shown at cursor

// ------------------------------------------------------------------
// Tool dispatch
// ------------------------------------------------------------------

function setTool(tool) {
  // Any pending operations end if the user picks a different tool —
  // cancel them so the schematic doesn't drift mid-operation. (Move
  // restores positions; anchor-pick simply forgets the request.)
  if (state.moveDraft) cancelMove();
  if (state.copyAnchorPending) cancelCopyAnchor();
  state.tool = tool;
  state.wireDraft = null;
  state.boxSelect = null;
  placementPreview = null;
  for (const b of document.querySelectorAll('.tool[data-tool]')) {
    b.classList.toggle('active', b.dataset.tool === tool);
  }
  // CSS hooks for cursor
  wrap.className = 'tool-' + tool;
  // Selection only persists in the 'select' tool.
  if (tool !== 'select') state.selectedIds.clear();
  // Sync the dropdown picker so it reflects the active tool.
  const picker = document.getElementById('part-picker');
  if (picker) {
    picker.value = (ELEM_TYPES[tool] || tool === 'WIRE') ? tool : '';
  }
  refreshProps();
  refreshHint();
  render();
}

function refreshHint() {
  const t = state.tool;
  let h;
  if (state.copyAnchorPending) {
    const verb = state.copyAnchorPending.cut ? 'cut' : 'copy';
    h = `Click to pick anchor point for ${verb}. <kbd>Esc</kbd> to cancel.`;
  } else if (state.moveDraft) {
    if (state.moveDraft.freshlyPasted) {
      h = 'Place paste: move the cursor, <kbd>click</kbd> to drop, <kbd>Esc</kbd> to cancel.';
    } else if (state.moveDraft.viaDrag) {
      h = 'Moving: release the mouse to drop, <kbd>Esc</kbd> to cancel.';
    } else {
      h = 'Moving: <kbd>click</kbd> to drop, <kbd>Esc</kbd> to cancel.';
    }
  } else if (state.wireDraft) {
    h = 'Wire: click to add a corner, <kbd>double-click</kbd> to finish, <kbd>Esc</kbd> to cancel.';
  } else if (t === 'select') {
    h = 'Select: click or drag a box. ' +
        '<kbd>M</kbd> or drag a selected item to move. ' +
        '<kbd>Ctrl+C</kbd>/<kbd>V</kbd> copy/paste, <kbd>Del</kbd> remove, ' +
        '<kbd>Space</kbd> rotate, <kbd>Esc</kbd> deselect.';
  } else if (t === 'delete') {
    h = 'Delete: click a part or wire to remove it.';
  } else if (t === 'rotate') {
    h = 'Rotate: click a part to rotate 90°.';
  } else if (t === 'WIRE') {
    h = 'Wire: click to start. Each click adds a Manhattan corner; <kbd>double-click</kbd> to finish.';
  } else if (ELEM_TYPES[t]) {
    h = `Place ${ELEM_TYPES[t].prefix}: click on the grid. <kbd>Space</kbd> rotates the ghost.`;
  } else {
    h = '';
  }
  hint.innerHTML = h +
    ' &middot; <kbd>F</kbd> fit &middot; ' +
    '<kbd>Shift</kbd>+drag pan &middot; wheel zoom.';
}

// ------------------------------------------------------------------
// Mouse handling
// ------------------------------------------------------------------

let panning = false;
let panStart = null;
// Track whether the current mouse-down→up sequence performed a drag,
// so the synthesised click event can be suppressed for box-selects.
let suppressNextClick = false;

wrap.addEventListener('mousedown', (e) => {
  // Each mousedown begins a fresh interaction — drop any leftover
  // suppress-flag from a prior drag. (Chrome does not synthesise a
  // click after a real drag, so the flag would otherwise linger.)
  suppressNextClick = false;

  // Anchor-pick mode swallows the mousedown so the next click lands
  // on `finalizeCopyAnchor` rather than starting a box-select / move.
  if (state.copyAnchorPending && e.button === 0) {
    e.preventDefault();
    return;
  }

  // Middle / right / shift-left → pan.
  if (e.button === 1 || e.button === 2 || (e.button === 0 && e.shiftKey)) {
    e.preventDefault();
    panning = true;
    panStart = { x: e.clientX, y: e.clientY,
                 px: state.pan.x, py: state.pan.y };
    wrap.classList.add('panning');
    return;
  }

  // Left-click in select tool: clicking on an item that's already in
  // the selection picks up a *move drag* (the user is rubber-banding
  // the whole selection); clicking on an unselected item with no
  // modifier pre-selects it and then picks up; clicking on empty
  // space starts a box-select.
  if (e.button === 0 && state.tool === 'select' && !state.moveDraft) {
    const world = eventToWorld(e);
    const hit = pickAt(world);
    if (hit) {
      const additive = e.shiftKey || e.ctrlKey || e.metaKey;
      // If the user clicked an unselected item with no modifier,
      // make it the sole selection before starting the drag.
      if (!state.selectedIds.has(hit.id) && !additive) {
        state.selectedIds.clear();
        state.selectedIds.add(hit.id);
        refreshProps();
      } else if (!state.selectedIds.has(hit.id) && additive) {
        // Modifier + unselected: just toggle selection in `click`.
        // Don't start a move drag here.
        return;
      }
      startMove([...state.selectedIds], snapPt(world),
                /*viaDrag=*/true, /*freshlyPasted=*/false);
      e.preventDefault();
      return;
    }
    state.boxSelect = { x0: world[0], y0: world[1],
                        x1: world[0], y1: world[1],
                        additive: e.ctrlKey || e.metaKey };
    e.preventDefault();
  }
});

wrap.addEventListener('mousemove', (e) => {
  if (panning) {
    state.pan.x = panStart.px + (e.clientX - panStart.x);
    state.pan.y = panStart.py + (e.clientY - panStart.y);
    render();
    return;
  }

  const world = eventToWorld(e);
  const cur = snapPt(world);
  state.cursorWorld = world;
  state.cursorInside = true;

  // Move-mode in progress — rubber-band every selected item by the
  // delta between the pickup and the current cursor.
  if (state.moveDraft) {
    updateMove(world);
    return;
  }

  // Box-select drag in progress
  if (state.boxSelect) {
    state.boxSelect.x1 = world[0];
    state.boxSelect.y1 = world[1];
    render();
    return;
  }

  // Wire draft preview cursor + Manhattan-axis hint
  if (state.wireDraft) {
    state.wireDraft.cursor = cur;
    const last = state.wireDraft.points[state.wireDraft.points.length - 1];
    const dx = Math.abs(cur[0] - last[0]);
    const dy = Math.abs(cur[1] - last[1]);
    state.wireDraft.axisFirst = dx >= dy ? 'h' : 'v';
    render();
    return;
  }

  // Placement preview ghost
  if (state.tool && ELEM_TYPES[state.tool]) {
    placementPreview = {
      type: state.tool,
      x: cur[0], y: cur[1],
      rot: previewRot,
      id: '',
      value: '',
    };
    render();
    return;
  }
  if (placementPreview) {
    placementPreview = null;
    render();
    return;
  }

  // Fallback: keep the grid-snap crosshair tracking the cursor in
  // tools that don't otherwise re-render on mousemove (select /
  // delete / rotate / WIRE-idle / anchor-pick).
  render();
});

wrap.addEventListener('mouseup', (e) => {
  if (panning) {
    panning = false;
    wrap.classList.remove('panning');
    return;
  }

  // Drag-driven move commits on mouseup. Paste/M-key moves wait for
  // the next click instead — this lets the cursor pick up items by
  // mousedown, drag, and release in one motion without the trailing
  // click double-firing the commit.
  if (state.moveDraft && state.moveDraft.viaDrag && e.button === 0) {
    commitMove();
    suppressNextClick = true;
    return;
  }

  // Finalise box-select on left-button release.
  if (state.boxSelect && e.button === 0) {
    const b = state.boxSelect;
    const x0 = Math.min(b.x0, b.x1), y0 = Math.min(b.y0, b.y1);
    const x1 = Math.max(b.x0, b.x1), y1 = Math.max(b.y0, b.y1);
    const minSize = 3;  // ignore micro-drags (treat as click)
    const isClick = (x1 - x0) < minSize && (y1 - y0) < minSize;
    if (!isClick) {
      // Pick parts whose bbox centre lies inside, and wires whose
      // every vertex lies inside (strict containment so a stray
      // half-overlapping wire doesn't get scooped up).
      if (!b.additive) state.selectedIds.clear();
      for (const p of state.parts) {
        const [bx0, by0, bx1, by1] = partBBox(p);
        const cx = (bx0 + bx1) / 2, cy = (by0 + by1) / 2;
        if (cx >= x0 && cx <= x1 && cy >= y0 && cy <= y1) {
          state.selectedIds.add(p.id);
        }
      }
      for (const w of state.wires) {
        if (w.points.every(([x, y]) =>
              x >= x0 && x <= x1 && y >= y0 && y <= y1)) {
          state.selectedIds.add(w.id);
        }
      }
      suppressNextClick = true;
      refreshProps();
    }
    state.boxSelect = null;
    render();
  }
});

wrap.addEventListener('mouseleave', () => {
  // Drop both the placement-preview ghost and the grid-snap crosshair
  // when the cursor leaves the canvas, so neither lingers at the last
  // recorded position.
  state.cursorInside = false;
  if (placementPreview) placementPreview = null;
  render();
});

wrap.addEventListener('contextmenu', (e) => e.preventDefault());

wrap.addEventListener('click', (e) => {
  if (panning) return;
  if (e.button !== 0) return;
  if (suppressNextClick) { suppressNextClick = false; return; }

  // Anchor-pick (after Ctrl+C/X) swallows the click and finalises
  // the clipboard with offsets relative to this point.
  if (state.copyAnchorPending) {
    finalizeCopyAnchor(snapPt(eventToWorld(e)));
    return;
  }

  // Paste / M-key moves commit on click rather than mouseup so the
  // user can move the cursor freely between mousedowns.
  if (state.moveDraft && !state.moveDraft.viaDrag) {
    commitMove();
    return;
  }

  const world = eventToWorld(e);
  const cur = snapPt(world);

  // Hit-test parts and wires (priority: parts > wires).
  const hit = pickAt(world);

  switch (state.tool) {
    case 'select': {
      if (!hit) {
        state.selectedIds.clear();
      } else if (e.shiftKey || e.ctrlKey || e.metaKey) {
        // Toggle membership when modifier held.
        if (state.selectedIds.has(hit.id)) state.selectedIds.delete(hit.id);
        else state.selectedIds.add(hit.id);
      } else {
        state.selectedIds.clear();
        state.selectedIds.add(hit.id);
      }
      refreshProps();
      render();
      break;
    }

    case 'delete':
      if (hit) {
        if (hit.kind === 'part') {
          state.parts = state.parts.filter(p => p.id !== hit.id);
        } else {
          state.wires = state.wires.filter(w => w.id !== hit.id);
        }
        state.selectedIds.delete(hit.id);
        pushHistory();
        render();
      }
      break;

    case 'rotate':
      if (hit && hit.kind === 'part') {
        const p = state.parts.find(p => p.id === hit.id);
        if (p) {
          p.rot = (p.rot + 90) % 360;
          pushHistory();
          render();
        }
      }
      break;

    case 'WIRE':
      handleWireClick(cur, world);
      break;

    default:
      // Element placement
      if (ELEM_TYPES[state.tool]) {
        addPart(state.tool, cur[0], cur[1], previewRot);
        pushHistory();
        render();
      }
      break;
  }
});

// Mouse wheel zoom (anchored to cursor).
wrap.addEventListener('wheel', (e) => {
  e.preventDefault();
  const factor = Math.exp(-e.deltaY * 0.0015);
  const newZoom = Math.max(0.2, Math.min(4, state.zoom * factor));
  if (newZoom === state.zoom) return;
  const rect = wrap.getBoundingClientRect();
  const ax = e.clientX - rect.left;
  const ay = e.clientY - rect.top;
  // Anchor: keep world point under cursor fixed.
  state.pan.x = ax - (ax - state.pan.x) * (newZoom / state.zoom);
  state.pan.y = ay - (ay - state.pan.y) * (newZoom / state.zoom);
  state.zoom = newZoom;
  render();
}, { passive: false });

// ------------------------------------------------------------------
// Hit testing
// ------------------------------------------------------------------
function pickAt(world) {
  const [wx, wy] = world;
  // Parts: check rotated bbox, padded by HIT_PAD/zoom slack.
  for (let i = state.parts.length - 1; i >= 0; i--) {
    const p = state.parts[i];
    const [x0, y0, x1, y1] = partBBox(p);
    if (wx >= x0 - HIT_PAD && wx <= x1 + HIT_PAD &&
        wy >= y0 - HIT_PAD && wy <= y1 + HIT_PAD) {
      return { kind: 'part', id: p.id };
    }
  }
  // Wires: distance from any segment.
  for (let i = state.wires.length - 1; i >= 0; i--) {
    const w = state.wires[i];
    for (let j = 0; j < w.points.length - 1; j++) {
      if (distToSeg(world, w.points[j], w.points[j + 1]) <= HIT_PAD) {
        return { kind: 'wire', id: w.id };
      }
    }
  }
  return null;
}

function distToSeg(p, a, b) {
  const dx = b[0] - a[0], dy = b[1] - a[1];
  const len2 = dx * dx + dy * dy;
  if (len2 === 0) return Math.hypot(p[0] - a[0], p[1] - a[1]);
  let t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / len2;
  t = Math.max(0, Math.min(1, t));
  const cx = a[0] + t * dx, cy = a[1] + t * dy;
  return Math.hypot(p[0] - cx, p[1] - cy);
}

// ------------------------------------------------------------------
// Mutators
// ------------------------------------------------------------------

function nextName(prefix) {
  const n = (state.nameCounters[prefix] || 0) + 1;
  state.nameCounters[prefix] = n;
  return `${prefix}${n}`;
}

function addPart(type, x, y, rot) {
  const meta = ELEM_TYPES[type];
  if (!meta) return;
  const id = nextName(meta.prefix);
  const value = type in DEFAULT_VALUES ? DEFAULT_VALUES[type] : id;
  state.parts.push({ id, type, x, y, rot, value });
}

// Manhattan wire builder.
//
// Each click extends the in-progress wire with an L from the previous
// corner to the click. The L's first axis follows wireDraft.axisFirst
// (set by the cursor heuristic in mousemove). The wire is finished
// when the user
//   (a) double-clicks (native dblclick event), OR
//   (b) clicks a *second* time on the same grid cell as the previous
//       corner — robust across browsers and across automation.
// `Esc` cancels the in-progress wire.
function handleWireClick(cur, world) {
  if (!state.wireDraft) {
    state.wireDraft = { points: [cur], cursor: cur, axisFirst: 'h' };
    refreshHint();
    render();
    return;
  }
  const last = state.wireDraft.points[state.wireDraft.points.length - 1];
  if (cur[0] === last[0] && cur[1] === last[1]) {
    // Same grid cell as the previous click — interpret as "finish".
    finalizeWireDraft();
    return;
  }
  const corners = lSegment(last, cur, state.wireDraft.axisFirst || 'h');
  state.wireDraft.points.push(...corners, cur);
  refreshHint();
  render();
}

function finalizeWireDraft() {
  const wd = state.wireDraft;
  if (!wd) return;
  if (wd.points.length >= 2) {
    addWire(wd.points);
    pushHistory();
  }
  state.wireDraft = null;
  refreshHint();
  render();
}

wrap.addEventListener('dblclick', (e) => {
  if (state.tool !== 'WIRE') return;
  e.preventDefault();
  finalizeWireDraft();
});

function addWire(points) {
  const id = `W${state.nextId++}`;
  state.wires.push({ id, points });
  // Connectivity merging happens lazily in the netlist generator —
  // any two coincident grid points get unioned there, so we don't
  // need to massage the wire-list structurally on insert.
}

// ------------------------------------------------------------------
// Properties pane
// ------------------------------------------------------------------
function refreshProps() {
  propPane.innerHTML = '';
  const sel = [...state.selectedIds];
  if (sel.length === 0) {
    propPane.innerHTML = '<div class="empty-msg">Click an element to inspect, or drag a box to select multiple.</div>';
    return;
  }
  if (sel.length > 1) {
    // Mixed-selection summary. Count parts vs wires for the header
    // and list the ids below.
    const np = sel.filter(id => state.parts.some(p => p.id === id)).length;
    const nw = sel.length - np;
    const parts = [];
    if (np) parts.push(`${np} part${np === 1 ? '' : 's'}`);
    if (nw) parts.push(`${nw} wire${nw === 1 ? '' : 's'}`);
    const info = document.createElement('div');
    info.innerHTML = `<strong>${parts.join(' + ')}</strong> selected. ` +
      `<kbd>M</kbd> move, <kbd>Ctrl+C</kbd>/<kbd>V</kbd> copy/paste, ` +
      `<kbd>Space</kbd> rotate, <kbd>Del</kbd> remove.`;
    info.style.cssText = 'font-size: 0.85rem; color: var(--fg);';
    propPane.appendChild(info);
    const list = document.createElement('div');
    list.style.cssText = 'margin-top: 8px; color: var(--muted); ' +
      'font-size: 0.78rem; font-family: "JetBrains Mono", monospace;';
    list.textContent = sel.join(', ');
    propPane.appendChild(list);
    return;
  }

  // Single-selection: could be a part or a wire.
  const wire = state.wires.find(w => w.id === sel[0]);
  if (wire) {
    const segs = wire.points.length - 1;
    let length = 0;
    for (let i = 1; i < wire.points.length; i++) {
      length += Math.abs(wire.points[i][0] - wire.points[i - 1][0]) +
                Math.abs(wire.points[i][1] - wire.points[i - 1][1]);
    }
    const lab = document.createElement('label');
    lab.innerHTML = `<span>Type</span><span style="font-weight:600">Wire</span>`;
    propPane.appendChild(lab);
    const lab2 = document.createElement('label');
    lab2.innerHTML = `<span>Id</span><span>${wire.id}</span>`;
    propPane.appendChild(lab2);
    const info = document.createElement('div');
    info.style.cssText = 'color: var(--muted); font-size: 0.78rem; margin-top: 6px;';
    info.textContent = `${segs} segment${segs === 1 ? '' : 's'}, ` +
                       `${length} px total ` +
                       `(${wire.points.length} vertices)`;
    propPane.appendChild(info);
    return;
  }

  const p = state.parts.find(p => p.id === sel[0]);
  if (!p) {
    propPane.innerHTML = '<div class="empty-msg">Selection lost.</div>';
    return;
  }
  const meta = ELEM_TYPES[p.type];
  const mk = (label, value, onCommit, mono = true) => {
    const lab = document.createElement('label');
    const sp = document.createElement('span'); sp.textContent = label;
    const inp = document.createElement('input');
    inp.type = 'text'; inp.value = value;
    if (!mono) inp.style.fontFamily = 'inherit';
    inp.addEventListener('change', () => onCommit(inp.value));
    inp.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') inp.blur();
    });
    lab.appendChild(sp); lab.appendChild(inp);
    propPane.appendChild(lab);
  };

  // Type is informational, not editable — render as a static row.
  {
    const lab = document.createElement('label');
    const sp = document.createElement('span'); sp.textContent = 'Type';
    const v = document.createElement('span');
    v.textContent = meta.label;
    v.style.cssText = 'font-weight: 600;';
    lab.appendChild(sp); lab.appendChild(v);
    propPane.appendChild(lab);
  }
  mk('Name', p.id, (v) => {
    if (!v || v === p.id) return;
    if (state.parts.some(q => q.id === v)) {
      alert(`Name "${v}" already in use.`);
      refreshProps();
      return;
    }
    state.selectedIds.delete(p.id);
    p.id = v;
    state.selectedIds.add(v);
    pushHistory();
    render();
    refreshProps();
  });
  if (p.type !== 'gnd') {
    mk('Value', p.value || '', (v) => {
      p.value = v;
      pushHistory();
      render();
    });
  }
  // Current-controlled sources need a controlling-V-source name.
  if (p.type === 'cccs' || p.type === 'ccvs') {
    mk('Ctrl V', p.ctrlSrc || '', (v) => {
      p.ctrlSrc = v;
      pushHistory();
      render();
    });
  }
  // Position info (read-only)
  const info = document.createElement('div');
  info.style.cssText = 'color: var(--muted); font-size: 0.78rem; margin-top: 6px;';
  info.textContent = `pos = (${p.x},${p.y})  rot = ${p.rot}°`;
  propPane.appendChild(info);
}

// ------------------------------------------------------------------
// Netlist generation
//
// Approach:
//   1. Each terminal sits at a snap-grid point. Build a union-find
//      where any two terminals at the same (x,y) are unified.
//   2. Wires unify the points along their polyline (corners included).
//   3. The connected component containing any GND part is node 0;
//      the rest get assigned 1, 2, ... by first-encounter order.
// ------------------------------------------------------------------
function updateNetlist() {
  netlistEl.value = generateNetlist();
}

function generateNetlist() {
  // Collect all canonical points: terminals + wire vertices.
  const dsu = new Map();  // key "x,y" -> root key
  const find = (k) => {
    const path = [];
    while (dsu.get(k) !== k) {
      path.push(k);
      k = dsu.get(k);
    }
    for (const p of path) dsu.set(p, k);
    return k;
  };
  const union = (a, b) => {
    const ra = find(a), rb = find(b);
    if (ra !== rb) dsu.set(ra, rb);
  };
  const seen = (k) => { if (!dsu.has(k)) dsu.set(k, k); };

  const partTerms = state.parts.map(p => ({
    p, terminals: partTerminals(p),
  }));

  for (const { terminals } of partTerms) {
    for (const t of terminals) seen(`${t.pos[0]},${t.pos[1]}`);
  }
  for (const w of state.wires) {
    for (const pt of w.points) seen(`${pt[0]},${pt[1]}`);
    for (let i = 1; i < w.points.length; i++) {
      union(`${w.points[i - 1][0]},${w.points[i - 1][1]}`,
            `${w.points[i][0]},${w.points[i][1]}`);
    }
  }

  // Assign node names. Ground roots become "0".
  const groundRoots = new Set();
  for (const { p } of partTerms) {
    if (p.type === 'gnd') {
      groundRoots.add(find(`${p.x},${p.y}`));
    }
  }
  const nodeMap = new Map();
  let nextNode = 1;
  const nodeOf = (k) => {
    const r = find(k);
    if (groundRoots.has(r)) return '0';
    if (!nodeMap.has(r)) nodeMap.set(r, String(nextNode++));
    return nodeMap.get(r);
  };

  // Build netlist lines.
  const lines = [];
  lines.push('* sycan circuit netlist');
  lines.push(`* generated ${new Date().toISOString()}`);
  lines.push('');

  // Output order matches SPICE convention by prefix: V, I, R, L, C,
  // D, Q, M, X (sub-circuit), then E/F/G/H controlled sources at the
  // end. Within a prefix, alphabetical by id.
  const prefixOrder = { V: 0, I: 1, R: 2, L: 3, C: 4, D: 5, Q: 6, M: 7, X: 8, E: 9, G: 10, F: 11, H: 12 };
  const sorted = state.parts.slice().sort((a, b) => {
    const pa = ELEM_TYPES[a.type]?.prefix ?? 'Z';
    const pb = ELEM_TYPES[b.type]?.prefix ?? 'Z';
    return (prefixOrder[pa] ?? 99) - (prefixOrder[pb] ?? 99)
        || a.id.localeCompare(b.id);
  });

  let emitted = 0;
  for (const p of sorted) {
    const meta = ELEM_TYPES[p.type];
    if (!meta || !meta.netlist) continue;
    // Resolve port name → node name lookup for this part. The kind's
    // emitter calls this for each port it cares about.
    const ports = partTerminals(p);
    const portNode = (name) => {
      const t = ports.find(t => t.name === name);
      return t ? nodeOf(`${t.pos[0]},${t.pos[1]}`) : '?';
    };
    const line = meta.netlist(p, portNode);
    if (line == null) continue;
    lines.push(line);
    emitted += 1;
  }
  if (emitted === 0) {
    lines.push('* (empty — place parts to populate)');
  }
  lines.push('.end');
  return lines.join('\n');
}

// ------------------------------------------------------------------
// Persistence: localStorage + JSON export/import
// ------------------------------------------------------------------
const LS_KEY = 'sycan.sedra.editor.v2';

function saveLocal() {
  try {
    localStorage.setItem(LS_KEY, JSON.stringify({
      parts: state.parts,
      wires: state.wires,
      nextId: state.nextId,
      nameCounters: state.nameCounters,
      pan: state.pan,
      zoom: state.zoom,
    }));
  } catch (_) { /* quota / private mode — silently ignore */ }
}

function loadLocal() {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (!raw) return false;
    const data = JSON.parse(raw);
    state.parts = data.parts || [];
    state.wires = data.wires || [];
    state.nextId = data.nextId || 1;
    state.nameCounters = data.nameCounters || {};
    if (data.pan) state.pan = data.pan;
    if (data.zoom) state.zoom = data.zoom;
    return true;
  } catch (_) { return false; }
}

function exportJson() {
  const blob = new Blob([JSON.stringify({
    version: 1,
    parts: state.parts,
    wires: state.wires,
    nameCounters: state.nameCounters,
  }, null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'circuit.json';
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

document.getElementById('btn-export-json').addEventListener('click', exportJson);
document.getElementById('btn-import-json').addEventListener('click', () => {
  document.getElementById('file-input').click();
});
document.getElementById('file-input').addEventListener('change', async (e) => {
  const f = e.target.files[0];
  if (!f) return;
  try {
    const text = await f.text();
    const data = JSON.parse(text);
    if (!data.parts || !Array.isArray(data.parts)) throw new Error('bad shape');
    state.parts = data.parts;
    state.wires = data.wires || [];
    state.nameCounters = data.nameCounters || {};
    state.nextId = Math.max(state.nextId,
      ...state.wires.map(w => parseInt(String(w.id).replace(/\D/g, '') || '0', 10) + 1));
    state.selectedIds.clear();
    pushHistory();
    refreshProps();
    render();
  } catch (err) {
    alert('Import failed: ' + err.message);
  }
  e.target.value = '';  // allow re-importing same file
});

document.getElementById('btn-copy').addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText(netlistEl.value);
    flashHint('Netlist copied to clipboard');
  } catch (_) {
    netlistEl.select();
    document.execCommand('copy');
    flashHint('Netlist copied to clipboard');
  }
});

let hintTimer = null;
function flashHint(msg) {
  hint.textContent = msg;
  clearTimeout(hintTimer);
  hintTimer = setTimeout(refreshHint, 1600);
}

// ------------------------------------------------------------------
// Undo / redo
// ------------------------------------------------------------------
function snapshot() {
  return JSON.stringify({
    parts: state.parts,
    wires: state.wires,
    nameCounters: state.nameCounters,
    nextId: state.nextId,
  });
}

function pushHistory() {
  // Every committed state is canonical Steiner-T form: any wire that
  // touches another wire's vertex or a part terminal mid-segment has
  // that point split into a vertex on its own polyline. Doing this in
  // pushHistory means a forgotten coalesce at a call-site is impossible.
  coalesceJunctions();
  history.length = historyIdx + 1;
  history.push(snapshot());
  if (history.length > HISTORY_LIMIT) history.shift();
  historyIdx = history.length - 1;
}

function restore(idx) {
  if (idx < 0 || idx >= history.length) return false;
  const data = JSON.parse(history[idx]);
  state.parts = data.parts;
  state.wires = data.wires;
  state.nameCounters = data.nameCounters;
  state.nextId = data.nextId;
  state.selectedIds.clear();
  state.wireDraft = null;
  state.boxSelect = null;
  state.moveDraft = null;
  wrap.classList.remove('moving');
  historyIdx = idx;
  return true;
}

document.getElementById('btn-undo').addEventListener('click', () => {
  if (historyIdx > 0 && restore(historyIdx - 1)) {
    refreshProps();
    render();
  }
});
document.getElementById('btn-redo').addEventListener('click', () => {
  if (historyIdx < history.length - 1 && restore(historyIdx + 1)) {
    refreshProps();
    render();
  }
});

document.getElementById('btn-clear').addEventListener('click', () => {
  if (state.parts.length === 0 && state.wires.length === 0) return;
  if (!confirm('Clear all parts and wires?')) return;
  state.parts = [];
  state.wires = [];
  state.nameCounters = {};
  state.selectedIds.clear();
  state.wireDraft = null;
  pushHistory();
  refreshProps();
  render();
});

document.getElementById('btn-fit').addEventListener('click', fitView);

function fitView() {
  if (state.parts.length === 0 && state.wires.length === 0) {
    state.pan.x = wrap.clientWidth / 2;
    state.pan.y = wrap.clientHeight / 2;
    state.zoom = 1;
    render();
    return;
  }
  let xmin = Infinity, ymin = Infinity, xmax = -Infinity, ymax = -Infinity;
  for (const p of state.parts) {
    const [x0, y0, x1, y1] = partBBox(p);
    xmin = Math.min(xmin, x0); ymin = Math.min(ymin, y0);
    xmax = Math.max(xmax, x1); ymax = Math.max(ymax, y1);
  }
  for (const w of state.wires) {
    for (const [x, y] of w.points) {
      xmin = Math.min(xmin, x); ymin = Math.min(ymin, y);
      xmax = Math.max(xmax, x); ymax = Math.max(ymax, y);
    }
  }
  const pad = 40;
  const W = wrap.clientWidth, H = wrap.clientHeight;
  const w = (xmax - xmin) + 2 * pad;
  const h = (ymax - ymin) + 2 * pad;
  state.zoom = Math.min(W / w, H / h, 2);
  state.pan.x = -xmin * state.zoom + (W - (xmax - xmin) * state.zoom) / 2;
  state.pan.y = -ymin * state.zoom + (H - (ymax - ymin) * state.zoom) / 2;
  render();
}

// ------------------------------------------------------------------
// Toolbar wiring + keyboard shortcuts
// ------------------------------------------------------------------
let previewRot = 0;

for (const b of document.querySelectorAll('.tool[data-tool]')) {
  b.addEventListener('click', () => setTool(b.dataset.tool));
}

// Components dropdown — select switches the tool to the chosen part.
document.getElementById('part-picker').addEventListener('change', (e) => {
  if (e.target.value) setTool(e.target.value);
});

document.addEventListener('keydown', (e) => {
  // Ignore when typing in an input/textarea — let native shortcuts win
  // there (Ctrl+C / Ctrl+V on the netlist box etc.).
  if (e.target.matches('input, textarea')) return;

  const k = e.key.toLowerCase();

  // Ctrl/Cmd + letter shortcuts (handled before the bare-letter map
  // so 'c' as copy doesn't switch to the capacitor tool).
  if (e.ctrlKey || e.metaKey) {
    if (k === 'z' && !e.shiftKey) {
      document.getElementById('btn-undo').click();
      e.preventDefault(); return;
    }
    if (k === 'y' || (k === 'z' && e.shiftKey)) {
      document.getElementById('btn-redo').click();
      e.preventDefault(); return;
    }
    if (k === 'c') {
      copySelection(/*cut=*/false);
      e.preventDefault(); return;
    }
    if (k === 'x') {
      copySelection(/*cut=*/true);
      e.preventDefault(); return;
    }
    if (k === 'v') {
      pasteClipboard();
      e.preventDefault(); return;
    }
    if (k === 'a') {
      state.selectedIds = new Set(state.parts.map(p => p.id));
      setTool('select');
      refreshProps();
      render();
      e.preventDefault(); return;
    }
    return;  // any other Ctrl/Cmd combo: don't intercept
  }

  // 'M' enters move mode for the current selection, tracking the
  // cursor (commits on next click; Esc cancels). Handled before the
  // single-letter tool map so it doesn't shadow the dedicated key.
  if (k === 'm' && !e.altKey) {
    if (state.selectedIds.size && !state.moveDraft) {
      startMove([...state.selectedIds],
                snapPt(state.cursorWorld),
                /*viaDrag=*/false, /*freshlyPasted=*/false);
      flashHint(`Moving ${state.selectedIds.size} item${state.selectedIds.size === 1 ? '' : 's'} — click to drop, Esc to cancel`);
      render();
    }
    e.preventDefault();
    return;
  }

  // Single-letter shortcuts. We keep them for the seven most-used kinds
  // — the rest are accessible via the components dropdown — and use
  // distinct letters for the editor ops (`s`/`x`/`b`) so they don't
  // collide with part letters (`d` is taken by the diode).
  const map = {
    'r': 'res',  'l': 'ind',  'c': 'cap',
    'v': 'vsrc', 'i': 'isrc',
    'd': 'diode',
    'w': 'WIRE', 'g': 'gnd',
    's': 'select', 'x': 'delete', 'b': 'rotate',
  };
  if (k in map && !e.altKey) {
    setTool(map[k]);
    e.preventDefault();
    return;
  }
  if (k === 'f' && !e.altKey) {
    fitView();
    e.preventDefault();
    return;
  }
  if (e.key === 'Escape') {
    if (state.copyAnchorPending) {
      cancelCopyAnchor();
    } else if (state.moveDraft) {
      cancelMove();
    } else if (state.wireDraft) {
      state.wireDraft = null;
      refreshHint();
      render();
    } else if (state.selectedIds.size) {
      state.selectedIds.clear();
      refreshProps();
      render();
    } else {
      setTool('select');
    }
    e.preventDefault();
    return;
  }
  if ((e.key === 'Delete' || e.key === 'Backspace') && state.selectedIds.size) {
    deleteSelection();
    e.preventDefault();
    return;
  }
  if (e.key === ' ') {
    if (state.moveDraft) {
      // Rotate the in-flight move (paste, drag, or M-key) around the
      // *live* anchor — `pickup + delta` is exactly where the user's
      // chosen anchor point is sitting right now, which on a paste is
      // the cursor.
      rotateMoveDraft();
    } else if (state.selectedIds.size) {
      let did = false;
      for (const id of state.selectedIds) {
        const p = state.parts.find(p => p.id === id);
        if (p) { p.rot = (p.rot + 90) % 360; did = true; }
      }
      if (did) { pushHistory(); render(); }
    } else if (ELEM_TYPES[state.tool]) {
      previewRot = (previewRot + 90) % 360;
      if (placementPreview) placementPreview.rot = previewRot;
      render();
    }
    e.preventDefault();
    return;
  }
});

// ------------------------------------------------------------------
// Copy / paste / delete (operate on `state.selectedIds`)
// ------------------------------------------------------------------

function deleteSelection() {
  if (!state.selectedIds.size) return;
  state.parts = state.parts.filter(p => !state.selectedIds.has(p.id));
  state.wires = state.wires.filter(w => !state.selectedIds.has(w.id));
  state.selectedIds.clear();
  pushHistory();
  refreshProps();
  render();
}

// Two-step copy/cut: stash the selection ids and let the next canvas
// click decide where the anchor sits. Until that click, the clipboard
// is *not* mutated — Esc cancels and leaves the previous clipboard
// untouched.
function copySelection(cut = false) {
  if (!state.selectedIds.size) return;
  // Cancel any in-flight move first so the click that picks the anchor
  // doesn't accidentally commit a move.
  if (state.moveDraft) cancelMove();
  state.copyAnchorPending = {
    selectedIds: [...state.selectedIds],
    cut: !!cut,
  };
  wrap.classList.add('picking-anchor');
  refreshHint();
  render();
}

function finalizeCopyAnchor(anchor) {
  const cap = state.copyAnchorPending;
  if (!cap) return;
  const idSet = new Set(cap.selectedIds);
  const selParts = state.parts.filter(p => idSet.has(p.id));
  const selWires = state.wires.filter(w => idSet.has(w.id));
  state.copyAnchorPending = null;
  wrap.classList.remove('picking-anchor');

  if (!selParts.length && !selWires.length) {
    refreshHint();
    render();
    return;
  }

  clipboard = {
    parts: selParts.map(p => ({
      type: p.type, dx: p.x - anchor[0], dy: p.y - anchor[1],
      rot: p.rot, value: p.value, ctrlSrc: p.ctrlSrc,
    })),
    wires: selWires.map(w => ({
      points: w.points.map(([x, y]) => [x - anchor[0], y - anchor[1]]),
    })),
  };

  if (cap.cut) {
    state.parts = state.parts.filter(p => !idSet.has(p.id));
    state.wires = state.wires.filter(w => !idSet.has(w.id));
    state.selectedIds.clear();
    pushHistory();
    refreshProps();
  }

  const np = selParts.length, nw = selWires.length;
  const frags = [];
  if (np) frags.push(`${np} part${np === 1 ? '' : 's'}`);
  if (nw) frags.push(`${nw} wire${nw === 1 ? '' : 's'}`);
  flashHint(
    `${cap.cut ? 'Cut' : 'Copied'} ${frags.join(' + ')} ` +
    `(anchor at ${anchor[0]}, ${anchor[1]})`
  );
  refreshHint();
  render();
}

function cancelCopyAnchor() {
  if (!state.copyAnchorPending) return;
  const cut = state.copyAnchorPending.cut;
  state.copyAnchorPending = null;
  wrap.classList.remove('picking-anchor');
  flashHint(cut ? 'Cut cancelled' : 'Copy cancelled');
  refreshHint();
  render();
}

function pasteClipboard() {
  const np = clipboard.parts ? clipboard.parts.length : 0;
  const nw = clipboard.wires ? clipboard.wires.length : 0;
  if (np + nw === 0) return;

  // Anchor at the current cursor (snapped). If the user hasn't put
  // the cursor over the canvas yet, anchor a couple of cells off the
  // origin so duplicates don't stack invisibly.
  let anchor = snapPt(state.cursorWorld);
  if (!anchor[0] && !anchor[1]) anchor = [GRID * 2, GRID * 2];

  const newIds = new Set();
  for (const c of clipboard.parts || []) {
    addPart(c.type, anchor[0] + c.dx, anchor[1] + c.dy, c.rot);
    const fresh = state.parts[state.parts.length - 1];
    if (c.value && c.type !== 'gnd') fresh.value = c.value;
    if (c.ctrlSrc) fresh.ctrlSrc = c.ctrlSrc;
    newIds.add(fresh.id);
  }
  for (const c of clipboard.wires || []) {
    const id = `W${state.nextId++}`;
    state.wires.push({
      id,
      points: c.points.map(([x, y]) => [anchor[0] + x, anchor[1] + y]),
    });
    newIds.add(id);
  }

  state.selectedIds = newIds;
  setTool('select');
  // Hand off to move-mode so the user can position the paste before
  // it's stamped down. Click to commit, Esc to throw it away.
  startMove([...newIds], anchor, /*viaDrag=*/false, /*freshlyPasted=*/true);
  refreshProps();
  refreshHint();
  render();
  const frags = [];
  if (np) frags.push(`${np} part${np === 1 ? '' : 's'}`);
  if (nw) frags.push(`${nw} wire${nw === 1 ? '' : 's'}`);
  flashHint(`Pasted ${frags.join(' + ')} — click to place, Esc to cancel`);
}

// ------------------------------------------------------------------
// Move
//
// `state.moveDraft` makes a temporary translation of every selected
// part + wire feel like a single rubber-banded drag. The originals
// stay snapshotted in `origs` so cancel can undo cleanly. Three ways
// to enter the mode:
//   - Mousedown on a selected item in the select tool (viaDrag=true,
//     commits on mouseup).
//   - 'M' key while there's a selection (viaDrag=false, commits on
//     the next click).
//   - Ctrl+V paste (viaDrag=false, freshlyPasted=true so cancel
//     removes the paste entirely).
// ------------------------------------------------------------------
function startMove(ids, pickup, viaDrag, freshlyPasted) {
  const origs = new Map();
  for (const id of ids) {
    const part = state.parts.find(p => p.id === id);
    if (part) {
      origs.set(id, { kind: 'part', x: part.x, y: part.y });
      continue;
    }
    const wire = state.wires.find(w => w.id === id);
    if (wire) {
      origs.set(id, { kind: 'wire', points: wire.points.map(pt => [...pt]) });
    }
  }
  if (!origs.size) return;
  state.moveDraft = {
    ids: [...ids],
    origs,
    pickup: [...pickup],
    delta: [0, 0],
    viaDrag: !!viaDrag,
    freshlyPasted: !!freshlyPasted,
  };
  wrap.classList.add('moving');
  refreshHint();
}

function updateMove(world) {
  const md = state.moveDraft;
  if (!md) return;
  const dx = snap(world[0] - md.pickup[0]);
  const dy = snap(world[1] - md.pickup[1]);
  if (dx === md.delta[0] && dy === md.delta[1]) return;
  md.delta = [dx, dy];
  for (const [id, orig] of md.origs) {
    if (orig.kind === 'part') {
      const part = state.parts.find(p => p.id === id);
      if (part) { part.x = orig.x + dx; part.y = orig.y + dy; }
    } else {
      const wire = state.wires.find(w => w.id === id);
      if (wire) {
        wire.points = orig.points.map(([x, y]) => [x + dx, y + dy]);
      }
    }
  }
  render();
}

// Rotate the entire move-draft 90° (CW in screen space) around the
// live anchor — `pickup + delta`, which on a paste is exactly the
// cursor (since the paste anchored the copy-anchor at the cursor) and
// on a drag/M-key move tracks the user's pickup point.
//
// Rotation does two things in lock-step: each part's *position* spins
// around the anchor, and each part's own `rot` increments by 90° so
// the body re-orients alongside its position. Wires get every vertex
// rotated. After applying the spin we re-snapshot `origs` and reset
// `delta` to (0, 0) so further cursor movement keeps translating
// from the now-rotated baseline.
function rotateMoveDraft() {
  const md = state.moveDraft;
  if (!md) return;
  const ax = md.pickup[0] + md.delta[0];
  const ay = md.pickup[1] + md.delta[1];
  // 90° CW in SVG coords: (dx, dy) → (-dy, dx).
  const spin = (x, y) => [ax - (y - ay), ay + (x - ax)];

  for (const [id, orig] of md.origs) {
    if (orig.kind === 'part') {
      const part = state.parts.find(p => p.id === id);
      if (part) {
        const [nx, ny] = spin(part.x, part.y);
        part.x = nx; part.y = ny;
        part.rot = (part.rot + 90) % 360;
      }
    } else {
      const wire = state.wires.find(w => w.id === id);
      if (wire) wire.points = wire.points.map(([x, y]) => spin(x, y));
    }
  }

  // Re-anchor the move so subsequent translations work from the new
  // rotated state. The world position of the anchor is unchanged
  // (rotation has a fixed point), so we just slide pickup → live
  // anchor and zero the delta.
  md.pickup = [ax, ay];
  md.delta = [0, 0];
  const newOrigs = new Map();
  for (const id of md.origs.keys()) {
    const part = state.parts.find(p => p.id === id);
    if (part) {
      newOrigs.set(id, { kind: 'part', x: part.x, y: part.y });
      continue;
    }
    const wire = state.wires.find(w => w.id === id);
    if (wire) {
      newOrigs.set(id, { kind: 'wire', points: wire.points.map(pt => [...pt]) });
    }
  }
  md.origs = newOrigs;

  render();
}

function commitMove() {
  if (!state.moveDraft) return;
  const md = state.moveDraft;
  state.moveDraft = null;
  wrap.classList.remove('moving');
  // Drop any zero-delta no-op straight into history-merging silence.
  // (`pushHistory` itself dedupes consecutive identical snapshots —
  // we rely on coalesceJunctions to rationalise the new positions.)
  pushHistory();
  refreshProps();
  refreshHint();
  render();
  if (md.viaDrag && (md.delta[0] || md.delta[1])) {
    flashHint(`Moved ${md.ids.length} item${md.ids.length === 1 ? '' : 's'}`);
  } else if (md.freshlyPasted) {
    flashHint(`Placed ${md.ids.length} item${md.ids.length === 1 ? '' : 's'}`);
  }
}

function cancelMove() {
  const md = state.moveDraft;
  if (!md) return;
  state.moveDraft = null;
  wrap.classList.remove('moving');
  if (md.freshlyPasted) {
    // The items only existed because of the paste — wipe them out.
    const idSet = new Set(md.ids);
    state.parts = state.parts.filter(p => !idSet.has(p.id));
    state.wires = state.wires.filter(w => !idSet.has(w.id));
    state.selectedIds.clear();
    flashHint('Paste cancelled');
  } else {
    // Restore originals from the snapshot.
    for (const [id, orig] of md.origs) {
      if (orig.kind === 'part') {
        const part = state.parts.find(p => p.id === id);
        if (part) { part.x = orig.x; part.y = orig.y; }
      } else {
        const wire = state.wires.find(w => w.id === id);
        if (wire) wire.points = orig.points;
      }
    }
    flashHint('Move cancelled');
  }
  refreshProps();
  refreshHint();
  render();
}

// ------------------------------------------------------------------
// Boot
// ------------------------------------------------------------------
async function init() {
  // Glyphs first — `drawPart` needs them to render anything. We hold
  // off on the initial render until the glyph fetches resolve, so we
  // never leak an "empty" snapshot into localStorage that would shadow
  // the user's saved circuit.
  await loadGlyphs();

  if (!loadLocal()) {
    state.pan.x = wrap.clientWidth / 2;
    state.pan.y = wrap.clientHeight / 2;
  }
  setTool('res');
  pushHistory();
  refreshProps();
  render();
}

window.addEventListener('resize', render);
init();
