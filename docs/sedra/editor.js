"use strict";
// ==================================================================
// editor.ts — interactive shell of the schematic editor
//
// State + history, mouse/keyboard/clipboard handling, the
// renderer, netlist generator, and persistence. Component
// definitions and glyph drawing live in glyphs.ts (loaded first;
// shared cross-file type declarations also come from there since
// `tsc --module none` keeps both files in the same global scope).
// ==================================================================
// ------------------------------------------------------------------
// State — single source of truth, serialised to localStorage.
// ------------------------------------------------------------------
const state = {
    parts: [],
    wires: [],
    nextId: 1, // monotonic counter for unique wire ids
    nameCounters: {}, // per-prefix counter, e.g. {R: 3, C: 1}
    tool: 'res',
    // Multi-selection: a Set of part / wire ids.
    selectedIds: new Set(),
    pan: { x: 0, y: 0 }, // screenX = pan.x + worldX * zoom
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
    boxSelect: null,
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
    copyAnchorPending: null,
    // Active move operation. While set, every render places parts and
    // wires at their snapshotted-origin position plus `delta`. Cleared
    // by `commitMove` (locks in the new positions and pushes history)
    // or `cancelMove` (restores originals; if `freshlyPasted`, deletes
    // the items entirely).
    moveDraft: null,
    // Calc Node mode: armed by clicking the "Calc Node" button.
    // While armed, the next canvas click picks the net to evaluate
    // (clicking either a wire or close enough to a part terminal).
    // After evaluation, we stash the resolved node name + the set of
    // grid points that belong to it so the renderer can outline the net
    // in the calc-node-highlight style. Cleared when the user picks a
    // different element, types a new label, or hits the button again.
    calcNode: { armed: false, mode: 'auto' },
    // Most recent calc-node result for the highlight overlay.
    calcNodeHighlight: null,
    // Net Highlight tool result. Set by clicking a wire / terminal while
    // tool === 'highlight'. The overlay covers every wire and terminal in
    // the connected component plus every wire elsewhere whose user label
    // matches the picked net's name (cross-component name propagation).
    // Cleared by clicking empty space, by structural edits (pushHistory),
    // and by Esc while the highlight tool is active.
    netHighlightOverlay: null,
    // Narrow segment selection. Two paths populate this Set:
    //
    //   * Click on a wire in the select tool → one entry under the
    //     cursor; the wire id also lands in ``selectedIds``.
    //   * Box-select drag → every segment whose two endpoints are
    //     strictly inside the rectangle is added (potentially many
    //     entries across many wires); each affected wire's id is also
    //     added to ``selectedIds`` so existing whole-wire operations
    //     (delete / copy / move) still apply.
    //
    // Render walks ``selectedSegments`` per wire to decide whether to
    // light up the whole polyline (no per-segment entries) or just the
    // listed sub-segments. Press ``u`` to promote a segment-level
    // selection to the whole net of the first picked wire.
    selectedSegments: new Set(),
};
// Composite key for a single segment of a wire. Used as the entry
// type for ``state.selectedSegments``.
function segKey(wireId, segIdx) {
    return `${wireId}|${segIdx}`;
}
// Returns true if any segment of ``wireId`` is in
// ``state.selectedSegments``.
function wireHasSegSel(wireId) {
    const prefix = wireId + '|';
    for (const k of state.selectedSegments) {
        if (k.startsWith(prefix))
            return true;
    }
    return false;
}
// Returns the indices of ``wireId``'s segments that are individually
// selected, in ascending order.
function wireSelectedSegments(wireId) {
    const prefix = wireId + '|';
    const out = [];
    for (const k of state.selectedSegments) {
        if (k.startsWith(prefix)) {
            out.push(Number(k.slice(prefix.length)));
        }
    }
    out.sort((a, b) => a - b);
    return out;
}
// Drop every entry for ``wireId`` from ``state.selectedSegments``.
function clearWireSegSel(wireId) {
    const prefix = wireId + '|';
    for (const k of [...state.selectedSegments]) {
        if (k.startsWith(prefix))
            state.selectedSegments.delete(k);
    }
}
// Mark *every* segment of ``wireId`` as selected. The wire id also
// goes into ``selectedIds`` so existing whole-wire bookkeeping (e.g.
// move drag membership) keeps working. Used wherever a code path
// wants to express "this wire is selected end-to-end" — segment-only
// is the source of truth, so the selection state always exposes one
// entry per segment instead of leaving a wire's render to fall
// through to a special whole-wire branch.
function selectWholeWire(wireId) {
    const w = state.wires.find(x => x.id === wireId);
    if (!w)
        return;
    state.selectedIds.add(wireId);
    for (let i = 0; i < w.points.length - 1; i++) {
        state.selectedSegments.add(segKey(wireId, i));
    }
}
// Walk a polyline's segments and group them into contiguous runs of
// the same selectedness. Used at drag-start to physically split a
// wire whose selection only covers some of its segments — the
// selected pieces become wires that move, the unselected pieces
// become wires that stay put.
function splitWireBySegments(points, selectedSegs) {
    const segCount = points.length - 1;
    if (segCount <= 0)
        return [];
    const out = [];
    let runStart = 0;
    let runSel = selectedSegs.has(0);
    for (let i = 1; i < segCount; i++) {
        const sel = selectedSegs.has(i);
        if (sel !== runSel) {
            out.push({
                points: points.slice(runStart, i + 1).map(p => [p[0], p[1]]),
                selected: runSel,
            });
            runStart = i;
            runSel = sel;
        }
    }
    out.push({
        points: points.slice(runStart, segCount + 1).map(p => [p[0], p[1]]),
        selected: runSel,
    });
    return out;
}
function deepCopyWires(wires) {
    return wires.map(w => {
        const copy = {
            id: w.id,
            points: w.points.map(p => [p[0], p[1]]),
        };
        if (w.label !== undefined)
            copy.label = w.label;
        return copy;
    });
}
// Locate every wire in `ids` that has *some but not all* of its
// segments selected and split it into pieces in place. Returns a
// new id list where each partially-selected wire has been replaced
// by the ids of its newly-extracted *selected* pieces (the
// unselected pieces become independent wires that aren't part of
// the move). Whole-wire selections (every segment marked) are
// returned unchanged.
function splitPartialWires(ids) {
    const out = [];
    for (const id of ids) {
        const w = state.wires.find(x => x.id === id);
        if (!w) {
            // Part id (or stale wire id) — pass through unchanged.
            out.push(id);
            continue;
        }
        const segCount = w.points.length - 1;
        if (segCount <= 0) {
            out.push(id);
            continue;
        }
        const selSegs = new Set(wireSelectedSegments(id));
        if (selSegs.size === 0 || selSegs.size === segCount) {
            // Either nothing or everything selected — no split needed.
            out.push(id);
            continue;
        }
        // Partial: split. Replace the original wire with pieces.
        const pieces = splitWireBySegments(w.points, selSegs);
        const idx = state.wires.indexOf(w);
        if (idx === -1) {
            out.push(id);
            continue;
        }
        state.selectedIds.delete(id);
        clearWireSegSel(id);
        state.wires.splice(idx, 1);
        const pieceWires = pieces.map(piece => {
            const newWire = {
                id: `W${state.nextId++}`,
                points: piece.points,
            };
            if (w.label !== undefined)
                newWire.label = w.label;
            return newWire;
        });
        state.wires.splice(idx, 0, ...pieceWires);
        for (let i = 0; i < pieces.length; i++) {
            const piece = pieces[i];
            const newWire = pieceWires[i];
            if (piece.selected) {
                state.selectedIds.add(newWire.id);
                for (let s = 0; s < newWire.points.length - 1; s++) {
                    state.selectedSegments.add(segKey(newWire.id, s));
                }
                out.push(newWire.id);
            }
        }
    }
    return out;
}
// Restore wires + selection from a `PreDragSnapshot`. Part positions
// come from the move-draft's origs map (parts aren't snapshotted in
// the wire-only struct). Used by both cancelMove and the parity-
// revert path in commitMove.
function restorePreDragSnapshot(md) {
    if (!md.preDragSnapshot)
        return;
    state.wires = deepCopyWires(md.preDragSnapshot.wires);
    state.nextId = md.preDragSnapshot.nextId;
    state.selectedIds = new Set(md.preDragSnapshot.selectedIds);
    state.selectedSegments = new Set(md.preDragSnapshot.selectedSegments);
    for (const [id, orig] of md.origs) {
        if (orig.kind === 'part') {
            const part = state.parts.find(p => p.id === id);
            if (part) {
                part.x = orig.x;
                part.y = orig.y;
            }
        }
    }
}
// Clipboard. Holds both parts and wires; positions are offsets from
// the user-picked anchor.
let clipboard = { parts: [], wires: [] };
// Undo/redo stack of serialised snapshots. Avoid the bare name
// `history` because that's also a DOM global (`window.history`).
const editHistory = [];
let historyIdx = -1;
const HISTORY_LIMIT = 100;
// ------------------------------------------------------------------
// DOM refs
//
// Every `getElementById` returns a node we know exists (the ID is
// declared in index.html). We use `!` for the initial assertion;
// after that the binding is statically typed.
// ------------------------------------------------------------------
// `var` (rather than `const`) so the binding lives at function/script
// scope and is visible to glyphs.ts's `drawPart` lexical reference
// without TS complaining about a const-let TDZ. Same idea for `wrap`.
var svg = document.getElementById('canvas');
var wrap = document.getElementById('canvas-wrap');
const hint = document.getElementById('hint');
const propPane = document.getElementById('prop-pane');
const netlistEl = document.getElementById('netlist');
const coords = document.getElementById('coords');
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
    if (!points.length)
        return '';
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
    if (from[0] === to[0] || from[1] === to[1])
        return [];
    return axisFirst === 'h'
        ? [[to[0], from[1]]] // horizontal first → corner shares to.x, from.y
        : [[from[0], to[1]]]; // vertical first   → corner shares from.x, to.y
}
// ------------------------------------------------------------------
// Render — full redraw on every state change. Cheap enough for the
// schematic sizes we care about, and trivially correct.
// ------------------------------------------------------------------
function render() {
    // Sync canvas viewBox to current pan/zoom.
    const W = wrap.clientWidth, H = wrap.clientHeight;
    svg.setAttribute('width', String(W));
    svg.setAttribute('height', String(H));
    svg.setAttribute('viewBox', `${-state.pan.x / state.zoom} ${-state.pan.y / state.zoom} ` +
        `${W / state.zoom} ${H / state.zoom}`);
    // Clear and rebuild
    while (svg.firstChild)
        svg.removeChild(svg.firstChild);
    // Layer 0: grid (drawn within the current viewBox)
    drawGrid();
    // Layer 1: wires. Selection in this editor lives at the segment
    // level — there is no "whole-wire selected" visual mode. Each
    // entry in ``state.selectedSegments`` paints one ``wire-selected``
    // overlay over its segment; a wire that's selected end-to-end
    // simply has every segment marked, and the overlapping overlays
    // visually equal a continuous highlight.
    //
    // The base polyline always renders plain — no class flip — so
    // clicking on (or box-selecting around) any segment never causes
    // a stylistic transition for the rest of the wire.
    for (const w of state.wires) {
        const selSegs = state.selectedIds.has(w.id)
            ? wireSelectedSegments(w.id)
            : [];
        el('path', { d: wirePath(w.points),
            class: 'wire',
            'data-id': w.id, 'data-kind': 'wire' }, svg);
        for (const i of selSegs) {
            if (i < 0 || i >= w.points.length - 1)
                continue;
            const a = w.points[i], b = w.points[i + 1];
            el('path', {
                d: `M${a[0]},${a[1]} L${b[0]},${b[1]}`,
                class: 'wire wire-selected',
                'pointer-events': 'none',
            }, svg);
        }
        // Invisible thicker hit-stroke for easier clicking.
        el('path', { d: wirePath(w.points), class: 'hit',
            'data-id': w.id, 'data-kind': 'wire' }, svg);
    }
    // Layer 1.5: net-label tags. We draw each labelled wire's name as a
    // small bordered tag riding the longest segment. Background rect
    // sized via getBBox after the text node lands, so font-metric
    // differences across browsers don't leave whitespace inside the box.
    drawNetLabels();
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
    // Layer 2.5: highlight the most recently picked calc-node net.
    drawCalcNodeHighlight();
    // Layer 2.6: Net Highlight overlay (orange wash on every wire and
    // terminal that belongs to the picked net, including cross-component
    // hits that share the same user label).
    drawNetHighlight();
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
        const viewW = wrap.clientWidth / state.zoom;
        const viewH = wrap.clientHeight / state.zoom;
        // Theme-aware crosshair: amber (#ffb405) on dark backgrounds,
        // dark navy (#003266) on light. The `--cross` CSS variable
        // declared in index.html resolves per `prefers-color-scheme`.
        const crossColor = 'var(--cross)';
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
    updateCoords();
    saveLocal();
}
// Live coordinate readout in the bottom-right of the canvas. While
// the user is dragging a box-select rectangle we show the
// `(x0, y0) → (x1, y1)` pair instead of the bare cursor. Hidden
// when there's no information to show (cursor off-canvas + no
// active box).
function updateCoords() {
    if (state.boxSelect) {
        const b = state.boxSelect;
        const [sx0, sy0] = snapPt([b.x0, b.y0]);
        const [sx1, sy1] = snapPt([b.x1, b.y1]);
        coords.textContent = `(${sx0}, ${sy0}) → (${sx1}, ${sy1})`;
        coords.classList.remove('hidden');
        return;
    }
    if (state.cursorInside) {
        const [cx, cy] = snapPt(state.cursorWorld);
        coords.textContent = `(${cx}, ${cy})`;
        coords.classList.remove('hidden');
        return;
    }
    coords.classList.add('hidden');
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
    if ((x1 - x0) / GRID > 600)
        return;
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
// Render net-label tags for every labelled net. After
// `propagateLabels` the same label may sit on every wire of a
// connected component; we draw exactly one tag per (component, label)
// pair so the canvas isn't littered with duplicate stickers. Within a
// component we pick the wire with the longest single segment so the
// tag has the most room to sit comfortably.
function drawNetLabels() {
    const wireRoot = wireComponentRoots();
    const longestSeg = (w) => {
        let best = 0;
        for (let i = 1; i < w.points.length; i++) {
            const len = Math.abs(w.points[i][0] - w.points[i - 1][0]) +
                Math.abs(w.points[i][1] - w.points[i - 1][1]);
            if (len > best)
                best = len;
        }
        return best;
    };
    const picked = new Map(); // `${root}|${label}` -> wire
    for (const w of state.wires) {
        const lab = sanitizeNetLabel(w.label);
        if (!lab)
            continue;
        if (!w.points.length)
            continue;
        const r = wireRoot.get(w.id);
        if (!r)
            continue;
        const key = `${r}|${lab}`;
        const cur = picked.get(key);
        if (!cur || longestSeg(w) > longestSeg(cur))
            picked.set(key, w);
    }
    for (const w of picked.values()) {
        const a = wireLabelAnchor(w);
        if (!a)
            continue;
        // Offset the tag a bit off the segment so it doesn't sit on the
        // wire stroke. Horizontal segments → above; vertical → to the
        // right (text stays horizontal either way).
        const dx = a.axis === 'v' ? 8 : 0;
        const dy = a.axis === 'h' ? -8 : 0;
        const tx = a.x + dx;
        const ty = a.y + dy;
        const text = el('text', {
            x: tx, y: ty,
            class: 'net-label-text',
            'text-anchor': a.axis === 'v' ? 'start' : 'middle',
            'dominant-baseline': a.axis === 'h' ? 'auto' : 'middle',
        }, svg);
        text.textContent = w.label || '';
        // Lay out the background rect by reading the rendered text bbox.
        let bb;
        try {
            bb = text.getBBox();
        }
        catch (_) {
            bb = null;
        }
        if (bb) {
            const pad = 3;
            const rect = el('rect', {
                x: bb.x - pad, y: bb.y - pad,
                width: bb.width + 2 * pad,
                height: bb.height + 2 * pad,
                class: 'net-label-bg',
            });
            // Insert behind the text so the text remains legible.
            svg.insertBefore(rect, text);
        }
    }
}
// Net Highlight overlay — orange wash on every wire and terminal in
// the picked net's grid-point set. The set is computed by
// `finalizeNetHighlight` and stored on `state.netHighlightOverlay`,
// using both the connected component (physical) and any cross-component
// wires whose user label matches the picked net's name.
function drawNetHighlight() {
    if (!state.netHighlightOverlay)
        return;
    const set = state.netHighlightOverlay.gridPoints;
    if (!set || !set.size)
        return;
    const isPt = (x, y) => set.has(`${x},${y}`);
    for (const w of state.wires) {
        // A wire belongs to the highlight if any of its vertices are in
        // the set — wires are normalised by `coalesceJunctions` so a
        // single wire never straddles two distinct nets.
        if (w.points.length && w.points.some(([x, y]) => isPt(x, y))) {
            el('path', {
                d: wirePath(w.points),
                class: 'net-highlight',
                'vector-effect': 'non-scaling-stroke',
            }, svg);
        }
    }
    for (const p of state.parts) {
        for (const t of partTerminals(p)) {
            if (isPt(t.pos[0], t.pos[1])) {
                el('circle', {
                    cx: t.pos[0], cy: t.pos[1], r: 6,
                    class: 'net-highlight-term',
                }, svg);
            }
        }
    }
}
// Strong dashed outline around the most recently picked calc-node net
// (parts at terminals + every wire path). Helps the user keep track
// of which node the on-pane expression refers to as they move on.
function drawCalcNodeHighlight() {
    if (!state.calcNodeHighlight)
        return;
    const set = state.calcNodeHighlight.gridPoints;
    if (!set || !set.size)
        return;
    const isPt = (x, y) => set.has(`${x},${y}`);
    for (const w of state.wires) {
        // Highlight a wire if every vertex of any sub-path lies in the set.
        // Cheap approximation: highlight the whole wire if any vertex is in
        // the set — wires almost always sit fully on a single net thanks
        // to coalesceJunctions, so cross-net wires would have to be drawn
        // deliberately.
        if (w.points.some(([x, y]) => isPt(x, y))) {
            el('path', { d: wirePath(w.points), class: 'calc-node-highlight' }, svg);
        }
    }
    // Ring each part terminal that sits on the net.
    for (const p of state.parts) {
        for (const t of partTerminals(p)) {
            if (isPt(t.pos[0], t.pos[1])) {
                el('circle', {
                    cx: t.pos[0], cy: t.pos[1], r: 7,
                    class: 'calc-node-highlight',
                }, svg);
            }
        }
    }
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
        for (const t of partTerminals(p))
            bump(t.pos, 1);
    }
    for (const w of state.wires) {
        if (w.points.length < 2)
            continue;
        bump(w.points[0], 1);
        bump(w.points[w.points.length - 1], 1);
        for (let i = 1; i < w.points.length - 1; i++)
            bump(w.points[i], 2);
    }
    for (const [k, count] of counts) {
        if (count < 3)
            continue;
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
            for (const t of partTerminals(p))
                points.push(t.pos);
        }
        for (const w of state.wires) {
            for (const pt of w.points)
                points.push(pt);
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
                    if (sameP(p, a) || sameP(p, b))
                        continue;
                    if (!pointOnSegment(p, a, b))
                        continue;
                    // Distance from `a` along the segment is just |dx|+|dy|
                    // for axis-aligned cases.
                    const d = Math.abs(p[0] - a[0]) + Math.abs(p[1] - a[1]);
                    interior.push({ p, d });
                }
                interior.sort((x, y) => x.d - y.d);
                let prev = a;
                for (const { p } of interior) {
                    if (sameP(p, prev))
                        continue;
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
                if (!sameP(out[i], dedup[dedup.length - 1]))
                    dedup.push(out[i]);
            }
            if (dedup.length !== w.points.length)
                w.points = dedup;
        }
        if (!changed)
            return;
    }
}
// ------------------------------------------------------------------
// End-to-end wire merging + interior simplification
//
// After Steiner-T coalescing, a freshly-drawn wire can sit adjacent
// to an existing wire of the same orientation without sharing a true
// junction with any third party. Likewise, the user might click a
// redundant corner along the wire-draft path that bends 0° (a
// straight click). Neither of those carry semantic meaning — they're
// just artifacts of incremental editing — so we collapse them in
// `pushHistory` so each "wire" object spans end-to-end, terminating
// only at a 90° turn, a Steiner point, or a free endpoint.
//
//   * `mergeCollinearWires`: two separate wires whose endpoints
//     coincide at point P and whose incident segments at P share an
//     axis (no 90° turn) merge into a single polyline. Skipped if a
//     third wire vertex or a part terminal also lives at P (that's a
//     T or terminal connection). Iterates until stable because one
//     merge can expose a fresh end-to-end pair at the far side.
//
//   * `simplifyCollinearVertices`: within a single wire, drop any
//     internal vertex Vi whose flanking segments are collinear AND
//     no *other* wire vertex / part terminal sits at Vi. The merge
//     pass leaves the seam point as an interior vertex; this pass
//     then erases it so the resulting wire reads as one straight
//     segment all the way through.
//
// ------------------------------------------------------------------
function mergeCollinearWires() {
    for (let iter = 0; iter < 64; iter++) {
        // (Re)build endpoint / interior / terminal incidence each pass —
        // a successful merge mutates `state.wires`, so cached indices
        // would go stale.
        const endpoints = new Map();
        const interiorCount = new Map();
        const terminalCount = new Map();
        for (const w of state.wires) {
            if (w.points.length < 2)
                continue;
            const fst = w.points[0];
            const lst = w.points[w.points.length - 1];
            const fkey = `${fst[0]},${fst[1]}`;
            const lkey = `${lst[0]},${lst[1]}`;
            let arr = endpoints.get(fkey);
            if (!arr) {
                arr = [];
                endpoints.set(fkey, arr);
            }
            arr.push({ wire: w, end: 'start' });
            arr = endpoints.get(lkey);
            if (!arr) {
                arr = [];
                endpoints.set(lkey, arr);
            }
            arr.push({ wire: w, end: 'end' });
            for (let i = 1; i < w.points.length - 1; i++) {
                const k = `${w.points[i][0]},${w.points[i][1]}`;
                interiorCount.set(k, (interiorCount.get(k) || 0) + 1);
            }
        }
        for (const p of state.parts) {
            for (const t of partTerminals(p)) {
                const k = `${t.pos[0]},${t.pos[1]}`;
                terminalCount.set(k, (terminalCount.get(k) || 0) + 1);
            }
        }
        // Look for a junction whose only inhabitants are exactly two wire
        // endpoints (no interior vertices, no terminals) AND whose two
        // incident segments share an axis.
        let didMerge = false;
        for (const [_key, ends] of endpoints) {
            if (ends.length !== 2)
                continue;
            const k = _key;
            if ((interiorCount.get(k) || 0) > 0)
                continue;
            if ((terminalCount.get(k) || 0) > 0)
                continue;
            const [a, b] = ends;
            if (a.wire === b.wire)
                continue; // closed loop on one wire
            const axisAt = (info) => {
                const pts = info.wire.points;
                if (info.end === 'start') {
                    return pts[0][0] === pts[1][0] ? 'v' : 'h';
                }
                const last = pts.length - 1;
                return pts[last - 1][0] === pts[last][0] ? 'v' : 'h';
            };
            if (axisAt(a) !== axisAt(b))
                continue; // 90° turn — preserve
            // If both sides carry distinct user labels, leave them alone.
            // Merging would silently drop one of the user's labels — the
            // netlist generator still surfaces a "conflicting net labels"
            // warning instead, which is actionable feedback. (One side
            // labelled and the other blank is fine: drop's label is
            // inherited below.)
            const labA = sanitizeNetLabel(a.wire.label);
            const labB = sanitizeNetLabel(b.wire.label);
            if (labA && labB && labA !== labB)
                continue;
            // Decide which wire to keep (lower-indexed in state.wires so
            // the older, user-named id wins).
            const idxA = state.wires.indexOf(a.wire);
            const idxB = state.wires.indexOf(b.wire);
            const keep = idxA <= idxB ? a : b;
            const drop = keep === a ? b : a;
            // Concatenate paths, dropping the seam point's duplicate. The
            // merged polyline writes the seam vertex *once*, as an interior
            // collinear vertex that `simplifyCollinearVertices` will then
            // erase on the next pass.
            let merged;
            if (keep.end === 'end' && drop.end === 'start') {
                merged = [...keep.wire.points, ...drop.wire.points.slice(1)];
            }
            else if (keep.end === 'start' && drop.end === 'end') {
                merged = [...drop.wire.points, ...keep.wire.points.slice(1)];
            }
            else if (keep.end === 'end' && drop.end === 'end') {
                merged = [...keep.wire.points, ...drop.wire.points.slice().reverse().slice(1)];
            }
            else {
                // both start at the seam → reverse keep so its 'start' becomes
                // the merged tail, then attach drop after the seam.
                merged = [...keep.wire.points.slice().reverse(), ...drop.wire.points.slice(1)];
            }
            keep.wire.points = merged;
            // Inherit the dropped wire's label if the keeper has none.
            if (!sanitizeNetLabel(keep.wire.label) &&
                sanitizeNetLabel(drop.wire.label)) {
                keep.wire.label = drop.wire.label;
            }
            state.wires = state.wires.filter(w => w !== drop.wire);
            // If either input wire participated in the selection, mark
            // the kept wire as selected end-to-end. Old per-segment
            // markers are dropped (they reference indices on the
            // pre-merge polylines) and replaced with a fresh full set
            // for the merged geometry.
            if (state.selectedIds.has(drop.wire.id)
                || state.selectedIds.has(keep.wire.id)) {
                clearWireSegSel(drop.wire.id);
                clearWireSegSel(keep.wire.id);
                state.selectedIds.delete(drop.wire.id);
                selectWholeWire(keep.wire.id);
            }
            didMerge = true;
            break;
        }
        if (!didMerge)
            return;
    }
}
function simplifyCollinearVertices() {
    // Every-grid-point incidence of *all* wire vertices and part
    // terminals — two indices we can subtract this wire's contribution
    // from to see whether anything else sits at a candidate vertex.
    const wireVertexCount = new Map();
    const terminalCount = new Map();
    for (const w of state.wires) {
        for (const pt of w.points) {
            const k = `${pt[0]},${pt[1]}`;
            wireVertexCount.set(k, (wireVertexCount.get(k) || 0) + 1);
        }
    }
    for (const p of state.parts) {
        for (const t of partTerminals(p)) {
            const k = `${t.pos[0]},${t.pos[1]}`;
            terminalCount.set(k, (terminalCount.get(k) || 0) + 1);
        }
    }
    for (const w of state.wires) {
        if (w.points.length < 3)
            continue;
        // Per-wire self-incidence so we can identify "other" wires at a
        // shared vertex (T-junction → keep) vs "only this wire" (drop).
        const ownAt = new Map();
        for (const pt of w.points) {
            const k = `${pt[0]},${pt[1]}`;
            ownAt.set(k, (ownAt.get(k) || 0) + 1);
        }
        const out = [w.points[0]];
        for (let i = 1; i < w.points.length - 1; i++) {
            const prev = w.points[i - 1];
            const cur = w.points[i];
            const next = w.points[i + 1];
            const collinear = (prev[0] === cur[0] && cur[0] === next[0])
                || (prev[1] === cur[1] && cur[1] === next[1]);
            if (!collinear) {
                out.push(cur);
                continue;
            }
            const k = `${cur[0]},${cur[1]}`;
            const otherWires = (wireVertexCount.get(k) || 0) - (ownAt.get(k) || 0);
            const terminals = terminalCount.get(k) || 0;
            if (otherWires > 0 || terminals > 0) {
                out.push(cur); // T-junction or terminal connection — keep.
            }
            // else: redundant collinear interior vertex — drop.
        }
        out.push(w.points[w.points.length - 1]);
        if (out.length !== w.points.length)
            w.points = out;
    }
}
// ------------------------------------------------------------------
// Connectivity helper
//
// Returns Map<wireId, componentRootKey>. Two wires share a root iff
// they are physically connected — either by sharing a vertex or by
// meeting at a part terminal. The returned root keys are opaque
// strings (just one of the underlying grid-point keys); only equality
// matters.
//
// `propagateLabels`, `drawNetLabels`, and the per-wire label commit
// all use this. The DSU itself is small and rebuilt each call — at
// schematic sizes we care about that's cheaper than threading a
// shared dirty flag through every mutator.
// ------------------------------------------------------------------
function wireComponentRoots() {
    const dsu = new Map();
    const find = (k) => {
        while (dsu.get(k) !== k) {
            dsu.set(k, dsu.get(dsu.get(k)));
            k = dsu.get(k);
        }
        return k;
    };
    const union = (a, b) => {
        const ra = find(a), rb = find(b);
        if (ra !== rb)
            dsu.set(ra, rb);
    };
    const seen = (k) => { if (!dsu.has(k))
        dsu.set(k, k); };
    // Part terminals participate in connectivity (two wires that meet at
    // a terminal must end up in the same component) but they don't carry
    // labels themselves.
    for (const p of state.parts) {
        for (const t of partTerminals(p))
            seen(`${t.pos[0]},${t.pos[1]}`);
    }
    for (const w of state.wires) {
        for (const pt of w.points)
            seen(`${pt[0]},${pt[1]}`);
        for (let i = 1; i < w.points.length; i++) {
            union(`${w.points[i - 1][0]},${w.points[i - 1][1]}`, `${w.points[i][0]},${w.points[i][1]}`);
        }
    }
    const wireRoot = new Map();
    for (const w of state.wires) {
        if (!w.points.length)
            continue;
        wireRoot.set(w.id, find(`${w.points[0][0]},${w.points[0][1]}`));
    }
    return wireRoot;
}
// Wires in the same connected component as `wire` (inclusive).
function wireIdsInSameComponent(wire) {
    const ids = new Set();
    if (!wire || !wire.points || !wire.points.length) {
        if (wire)
            ids.add(wire.id);
        return ids;
    }
    const wireRoot = wireComponentRoots();
    const target = wireRoot.get(wire.id);
    if (!target) {
        ids.add(wire.id);
        return ids;
    }
    for (const [id, root] of wireRoot)
        if (root === target)
            ids.add(id);
    return ids;
}
// ------------------------------------------------------------------
// Drag-mode helpers
//
// `netSignature` builds a normalised string from the current
// netlist's union-find: every part-terminal is binned into a group
// keyed by its DSU root, the groups are sorted, and the result is a
// stable representation of "which terminals are connected to which".
// Used by the drag-mode parity check — comparing the pre- and post-
// drag signatures catches stray T-joints, broken connections, and
// rerouted wires that accidentally cross another terminal.
//
// (The end-of-wire bend itself happens inline in `updateMove`'s
// `wire-spanning` branch — we keep the original middle vertices and
// only nudge / corner the segment immediately adjacent to the
// dragged endpoint, mirroring the "drag-the-end" behaviour you'd
// expect from KiCad / Altium.)
// ------------------------------------------------------------------
function netSignature() {
    const nl = buildNetlist();
    const groups = new Map();
    for (const p of state.parts) {
        for (const t of partTerminals(p)) {
            const node = nl.nodeAt(t.pos);
            const tid = `${p.id}:${t.name}`;
            let s = groups.get(node);
            if (!s) {
                s = new Set();
                groups.set(node, s);
            }
            s.add(tid);
        }
    }
    // Drop the (auto-numbered) node names — only the *partition* matters.
    const parts = [];
    for (const s of groups.values())
        parts.push([...s].sort().join('|'));
    parts.sort();
    return parts.join('\n');
}
// ------------------------------------------------------------------
// Net-label propagation
//
// Companion to `coalesceJunctions`. After the schematic is in canonical
// Steiner-T form we know which wires share a connected component (any
// two wires whose vertex sets touch — through a shared vertex or via
// a part terminal). Within each component:
//
//   * If exactly one distinct user label appears, every *unlabelled*
//     wire in the same component inherits that label. This is the
//     "named has priority over unnamed" rule — drawing a fresh wire
//     into a labelled net silently extends the label to the new wire.
//
//   * If multiple distinct labels appear, we leave them alone. The
//     netlist generator surfaces a warning, and silently overwriting
//     one of two user-supplied names would be surprising. The
//     refreshProps label-commit handler explicitly normalises the
//     component name on user rename, so multiple distinct labels
//     normally only arise from JSON imports or undo into mid-edit
//     states.
//
//   * If no labels appear, nothing happens.
//
// Run from `pushHistory` so every committed state has labels in their
// fully-propagated form. That makes the in-memory model authoritative:
// the Net Highlight tool, the netlist generator, and any future
// feature that asks "what label sits on this wire?" can read
// `wire.label` directly without re-deriving the propagation.
// ------------------------------------------------------------------
function propagateLabels() {
    const wireRoot = wireComponentRoots();
    // root -> Set(labels). Multiple distinct entries → no propagation.
    const labelsByRoot = new Map();
    for (const w of state.wires) {
        const lab = sanitizeNetLabel(w.label);
        if (!lab)
            continue;
        const r = wireRoot.get(w.id);
        if (!r)
            continue;
        let s = labelsByRoot.get(r);
        if (!s) {
            s = new Set();
            labelsByRoot.set(r, s);
        }
        s.add(lab);
    }
    for (const w of state.wires) {
        if (sanitizeNetLabel(w.label))
            continue; // already named
        const r = wireRoot.get(w.id);
        if (!r)
            continue;
        const labels = labelsByRoot.get(r);
        if (!labels || labels.size !== 1)
            continue;
        const [only] = labels;
        w.label = only;
    }
}
function drawWirePreview() {
    const wd = state.wireDraft;
    if (!wd)
        return;
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
// Ghost part shown at cursor while an element-placement tool is active.
let placementPreview = null;
// ------------------------------------------------------------------
// Tool dispatch
// ------------------------------------------------------------------
function setTool(tool) {
    // Any pending operations end if the user picks a different tool —
    // cancel them so the schematic doesn't drift mid-operation. (Move
    // restores positions; anchor-pick simply forgets the request.)
    if (state.moveDraft)
        cancelMove();
    if (state.copyAnchorPending)
        cancelCopyAnchor();
    if (state.calcNode.armed)
        cancelCalcNodePick();
    state.tool = tool;
    state.wireDraft = null;
    state.boxSelect = null;
    state.selectedSegments.clear();
    placementPreview = null;
    for (const b of document.querySelectorAll('.tool[data-tool]')) {
        b.classList.toggle('active', b.dataset['tool'] === tool);
    }
    // CSS hooks for cursor
    wrap.className = 'tool-' + tool;
    // Selection only persists in the 'select' tool.
    if (tool !== 'select')
        state.selectedIds.clear();
    // Sync the dropdown picker so it reflects the active tool.
    const picker = document.getElementById('part-picker');
    if (picker) {
        picker.value = (isElemKind(tool) || tool === 'WIRE') ? tool : '';
    }
    refreshProps();
    refreshHint();
    render();
}
function isElemKind(t) {
    return Object.prototype.hasOwnProperty.call(ELEM_TYPES, t);
}
function refreshHint() {
    const t = state.tool;
    let h;
    if (state.copyAnchorPending) {
        const verb = state.copyAnchorPending.cut ? 'cut' : 'copy';
        h = `Click to pick anchor point for ${verb}. <kbd>Esc</kbd> to cancel.`;
    }
    else if (state.calcNode.armed) {
        h = 'Calc Node: click on a wire or part terminal to compute its ' +
            'symbolic voltage. <kbd>Esc</kbd> to cancel.';
    }
    else if (state.moveDraft) {
        if (state.moveDraft.freshlyPasted) {
            h = 'Place paste: move the cursor, <kbd>click</kbd> to drop, <kbd>Esc</kbd> to cancel.';
        }
        else if (state.moveDraft.viaDrag) {
            h = 'Moving: release the mouse to drop, <kbd>Esc</kbd> to cancel.';
        }
        else {
            h = 'Moving: <kbd>click</kbd> to drop, <kbd>Esc</kbd> to cancel.';
        }
    }
    else if (state.wireDraft) {
        h = 'Wire: click to add a corner, <kbd>double-click</kbd> to finish, <kbd>Esc</kbd> to cancel.';
    }
    else if (t === 'select') {
        h = 'Select: click a wire to pick a segment, <kbd>U</kbd> expands ' +
            'to the whole net. Drag a box for multi-select. ' +
            '<kbd>M</kbd> or drag a selected item to move. ' +
            '<kbd>Ctrl+C</kbd>/<kbd>V</kbd> copy/paste, <kbd>Del</kbd> remove, ' +
            '<kbd>Space</kbd> rotate, <kbd>Esc</kbd> deselect.';
    }
    else if (t === 'delete') {
        h = 'Delete: click a part or wire to remove it.';
    }
    else if (t === 'rotate') {
        h = 'Rotate: click a part to rotate 90°.';
    }
    else if (t === 'highlight') {
        h = 'Net Highlight: click a wire or terminal to wash its net. ' +
            'Wires elsewhere with the same label join the highlight. ' +
            'Click empty space to clear.';
    }
    else if (t === 'WIRE') {
        h = 'Wire: click to start. Each click adds a Manhattan corner; <kbd>double-click</kbd> to finish.';
    }
    else if (isElemKind(t)) {
        h = `Place ${ELEM_TYPES[t].prefix}: click on the grid. <kbd>Space</kbd> rotates the ghost.`;
    }
    else {
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
    // Anchor-pick / calc-node-pick swallow the mousedown so the next
    // click lands on the dedicated finalisers rather than starting a
    // box-select / move.
    if ((state.copyAnchorPending || state.calcNode.armed) && e.button === 0) {
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
    // Left-click in select tool. The unit of selection is a *line
    // segment* for wires and the part itself for parts, with no
    // intermediate "whole-wire" abstraction. The handler pivots on
    // whether the exact thing under the cursor is already selected:
    //
    //   * Already selected (segment in ``selectedSegments`` or part
    //     in ``selectedIds``) → start a move drag with the current
    //     selection unchanged. This preserves the visible
    //     highlights so that clicking a multi-selected item never
    //     mutates the visual state.
    //   * Not yet selected, no modifier → replace the selection with
    //     just this target and start the drag.
    //   * Not yet selected, with modifier → defer to the ``click``
    //     event, which toggles membership.
    //
    // Empty-space clicks fall through to box-select.
    if (e.button === 0 && state.tool === 'select' && !state.moveDraft) {
        const world = eventToWorld(e);
        const hit = pickAt(world);
        if (hit) {
            const additive = e.shiftKey || e.ctrlKey || e.metaKey;
            // For a wire hit, the actual unit being picked is the specific
            // segment under the cursor. Compute its segKey so we can ask
            // the same "is this thing selected?" question for both wires
            // and parts.
            let segTarget = null;
            if (hit.kind === 'wire') {
                const w = state.wires.find(x => x.id === hit.id);
                if (w && w.points.length >= 2) {
                    const idx = closestSegmentIndex(world, w);
                    segTarget = { wireId: hit.id, key: segKey(hit.id, idx), idx };
                }
            }
            const alreadySelected = segTarget
                ? state.selectedSegments.has(segTarget.key)
                : state.selectedIds.has(hit.id);
            if (additive) {
                // Toggle membership is the click handler's job; don't start
                // a drag here.
                return;
            }
            if (!alreadySelected) {
                // Replace the selection with just this target. Clearing
                // ``selectedSegments`` only affects entries on *other*
                // wires; the new target is added back below.
                state.selectedIds.clear();
                state.selectedSegments.clear();
                if (segTarget) {
                    state.selectedIds.add(segTarget.wireId);
                    state.selectedSegments.add(segTarget.key);
                }
                else {
                    state.selectedIds.add(hit.id);
                }
                refreshProps();
            }
            startMove([...state.selectedIds], snapPt(world), 
            /*viaDrag=*/ true, /*freshlyPasted=*/ false);
            e.preventDefault();
            return;
        }
        state.boxSelect = { x0: world[0], y0: world[1],
            x1: world[0], y1: world[1],
            additive: e.ctrlKey || e.metaKey };
        if (!state.boxSelect.additive) {
            state.selectedSegments.clear();
        }
        e.preventDefault();
    }
});
wrap.addEventListener('mousemove', (e) => {
    if (panning && panStart) {
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
    if (state.tool && isElemKind(state.tool)) {
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
        const minSize = 3; // ignore micro-drags (treat as click)
        const isClick = (x1 - x0) < minSize && (y1 - y0) < minSize;
        if (!isClick) {
            // Parts: bbox-centre containment (unchanged — a part is
            // atomic). Wires: pick at *segment* granularity instead of
            // requiring every vertex inside, so a box that crosses through
            // a multi-segment wire selects only the segments whose two
            // endpoints fall inside. Each affected wire's id is also added
            // to ``selectedIds`` so the existing whole-wire delete / copy /
            // move semantics still apply.
            if (!b.additive) {
                state.selectedIds.clear();
                state.selectedSegments.clear();
            }
            for (const p of state.parts) {
                const [bx0, by0, bx1, by1] = partBBox(p);
                const cx = (bx0 + bx1) / 2, cy = (by0 + by1) / 2;
                if (cx >= x0 && cx <= x1 && cy >= y0 && cy <= y1) {
                    state.selectedIds.add(p.id);
                }
            }
            const inside = ([x, y]) => x >= x0 && x <= x1 && y >= y0 && y <= y1;
            for (const w of state.wires) {
                let pickedAny = false;
                for (let i = 0; i < w.points.length - 1; i++) {
                    if (inside(w.points[i]) && inside(w.points[i + 1])) {
                        state.selectedSegments.add(segKey(w.id, i));
                        pickedAny = true;
                    }
                }
                if (pickedAny)
                    state.selectedIds.add(w.id);
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
    if (placementPreview)
        placementPreview = null;
    render();
});
wrap.addEventListener('contextmenu', (e) => e.preventDefault());
wrap.addEventListener('click', (e) => {
    if (panning)
        return;
    if (e.button !== 0)
        return;
    if (suppressNextClick) {
        suppressNextClick = false;
        return;
    }
    // Anchor-pick (after Ctrl+C/X) swallows the click and finalises
    // the clipboard with offsets relative to this point.
    if (state.copyAnchorPending) {
        finalizeCopyAnchor(snapPt(eventToWorld(e)));
        return;
    }
    // Calc-node pick swallows the click, resolves the net under the
    // cursor, and runs the symbolic solver.
    if (state.calcNode.armed) {
        finalizeCalcNodePick(snapPt(eventToWorld(e)), eventToWorld(e));
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
            // mousedown handles the simple-click-on-hit case (it sets the
            // segment-only highlights there and starts a move-draft). This
            // arm fires for empty-space clicks and modifier-toggle clicks,
            // so we always drop the segment markers — additive selections
            // span multiple things and segment-only is a transient visual.
            state.selectedSegments.clear();
            if (!hit) {
                state.selectedIds.clear();
            }
            else if (e.shiftKey || e.ctrlKey || e.metaKey) {
                // Toggle membership when modifier held.
                if (state.selectedIds.has(hit.id))
                    state.selectedIds.delete(hit.id);
                else
                    state.selectedIds.add(hit.id);
            }
            else {
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
                }
                else {
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
        case 'highlight':
            finalizeNetHighlight(cur, world);
            break;
        default:
            // Element placement
            if (isElemKind(state.tool)) {
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
    if (newZoom === state.zoom)
        return;
    const rect = wrap.getBoundingClientRect();
    const ax = e.clientX - rect.left;
    const ay = e.clientY - rect.top;
    // Anchor: keep world point under cursor fixed.
    state.pan.x = ax - (ax - state.pan.x) * (newZoom / state.zoom);
    state.pan.y = ay - (ay - state.pan.y) * (newZoom / state.zoom);
    state.zoom = newZoom;
    render();
}, { passive: false });
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
    if (len2 === 0)
        return Math.hypot(p[0] - a[0], p[1] - a[1]);
    let t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / len2;
    t = Math.max(0, Math.min(1, t));
    const cx = a[0] + t * dx, cy = a[1] + t * dy;
    return Math.hypot(p[0] - cx, p[1] - cy);
}
// Within a single wire, pick the segment index whose perpendicular
// distance to `world` is smallest. Used by the select-tool to choose
// which segment of an end-to-end-merged wire the user is pointing at.
function closestSegmentIndex(world, wire) {
    let best = 0, bestD = Infinity;
    for (let i = 0; i < wire.points.length - 1; i++) {
        const d = distToSeg(world, wire.points[i], wire.points[i + 1]);
        if (d < bestD) {
            bestD = d;
            best = i;
        }
    }
    return best;
}
// Walk the same connectivity DSU `wireComponentRoots` builds, but
// keep the underlying root for a given seed wire and bucket every
// wire id whose vertex shares it, plus every part id with at least
// one terminal on the same root. Used by the `u` key to expand a
// segment selection to the full electrical net (wires + connected
// parts). Wire vertices and part terminals are both unioned by
// coordinate equality, so a wire that ends at a part terminal pulls
// the part into the same component without any extra plumbing.
function netMembers(seedWire) {
    const dsu = new Map();
    const find = (k) => {
        while (dsu.get(k) !== k) {
            dsu.set(k, dsu.get(dsu.get(k)));
            k = dsu.get(k);
        }
        return k;
    };
    const union = (a, b) => {
        const ra = find(a), rb = find(b);
        if (ra !== rb)
            dsu.set(ra, rb);
    };
    const seen = (k) => { if (!dsu.has(k))
        dsu.set(k, k); };
    for (const p of state.parts) {
        for (const t of partTerminals(p))
            seen(`${t.pos[0]},${t.pos[1]}`);
    }
    for (const w of state.wires) {
        for (const pt of w.points)
            seen(`${pt[0]},${pt[1]}`);
        for (let i = 1; i < w.points.length; i++) {
            union(`${w.points[i - 1][0]},${w.points[i - 1][1]}`, `${w.points[i][0]},${w.points[i][1]}`);
        }
    }
    const wireIds = new Set();
    const partIds = new Set();
    if (!seedWire.points.length)
        return { wireIds, partIds };
    const target = find(`${seedWire.points[0][0]},${seedWire.points[0][1]}`);
    for (const w of state.wires) {
        if (!w.points.length)
            continue;
        if (find(`${w.points[0][0]},${w.points[0][1]}`) === target) {
            wireIds.add(w.id);
        }
    }
    for (const p of state.parts) {
        for (const t of partTerminals(p)) {
            if (find(`${t.pos[0]},${t.pos[1]}`) === target) {
                partIds.add(p.id);
                break;
            }
        }
    }
    return { wireIds, partIds };
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
    if (!meta)
        return;
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
function handleWireClick(cur, _world) {
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
    if (!wd)
        return;
    if (wd.points.length >= 2) {
        addWire(wd.points);
        pushHistory();
    }
    state.wireDraft = null;
    refreshHint();
    render();
}
wrap.addEventListener('dblclick', (e) => {
    if (state.tool !== 'WIRE')
        return;
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
        if (np)
            parts.push(`${np} part${np === 1 ? '' : 's'}`);
        if (nw)
            parts.push(`${nw} wire${nw === 1 ? '' : 's'}`);
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
        // Editable net label. The same label propagates to every wire in
        // the same connected component via propagateLabels, but the user
        // edits it on a single wire in this panel.
        const labRow = document.createElement('label');
        const sp = document.createElement('span');
        sp.textContent = 'Net label';
        const inp = document.createElement('input');
        inp.type = 'text';
        inp.value = wire.label || '';
        inp.placeholder = 'in / out / vbias';
        inp.title = 'Letters, digits and underscore. Must contain a letter ' +
            'and may not be "0" (reserved for ground).';
        inp.addEventListener('change', () => {
            const raw = inp.value.trim();
            // Whether we're naming, renaming, or unnaming, we apply the
            // change to every wire in the same connected component. This
            // keeps the schematic in a single canonical state — without it,
            // unnaming one wire would just be undone by `propagateLabels`
            // re-filling the name from a still-labelled sibling, and
            // renaming one wire would leave a half-renamed component that
            // surfaces a netlist warning.
            const compIds = wireIdsInSameComponent(wire);
            if (!raw) {
                for (const w of state.wires)
                    if (compIds.has(w.id))
                        delete w.label;
                pushHistory();
                render();
                refreshProps();
                return;
            }
            const cleaned = sanitizeNetLabel(raw);
            if (!cleaned) {
                alert(`"${raw}" is not a valid net label. Use letters, digits ` +
                    `and "_"; must contain a letter and may not be "0".`);
                inp.value = wire.label || '';
                return;
            }
            for (const w of state.wires)
                if (compIds.has(w.id))
                    w.label = cleaned;
            pushHistory();
            render();
            refreshProps();
        });
        inp.addEventListener('keydown', (e) => {
            if (e.key === 'Enter')
                inp.blur();
        });
        labRow.appendChild(sp);
        labRow.appendChild(inp);
        propPane.appendChild(labRow);
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
        const sp = document.createElement('span');
        sp.textContent = label;
        const inp = document.createElement('input');
        inp.type = 'text';
        inp.value = value;
        if (!mono)
            inp.style.fontFamily = 'inherit';
        inp.addEventListener('change', () => onCommit(inp.value));
        inp.addEventListener('keydown', (e) => {
            if (e.key === 'Enter')
                inp.blur();
        });
        lab.appendChild(sp);
        lab.appendChild(inp);
        propPane.appendChild(lab);
    };
    // Type is informational, not editable — render as a static row.
    {
        const lab = document.createElement('label');
        const sp = document.createElement('span');
        sp.textContent = 'Type';
        const v = document.createElement('span');
        v.textContent = meta.label;
        v.style.cssText = 'font-weight: 600;';
        lab.appendChild(sp);
        lab.appendChild(v);
        propPane.appendChild(lab);
    }
    mk('Name', p.id, (v) => {
        if (!v || v === p.id)
            return;
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
    netlistEl.value = buildNetlist().text;
}
// Net label rules:
//   * Allowed characters: A-Z a-z 0-9 _ . Must contain at least one
//     letter (so it can't collide with the auto-numbered 1, 2, 3, ...
//     SPICE node names) and must not equal "0" (reserved for ground).
//   * Returned in lower-case (SPICE is case-insensitive about nodes;
//     using a single case keeps `nc_plus` and `NC_PLUS` from colliding
//     across two wires).
//   * Returns null for invalid labels — caller should warn or fall
//     back to the auto-numbered name.
function sanitizeNetLabel(raw) {
    if (typeof raw !== 'string')
        return null;
    const s = raw.trim();
    if (!s)
        return null;
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(s))
        return null;
    if (s.toLowerCase() === 'gnd' || s === '0')
        return null;
    return s;
}
// Build the netlist *and* expose the union-find that produced it so
// other features (Calc Node, label visualisation) can ask "which node
// name lives at this grid point?" without re-running the analysis.
function buildNetlist() {
    // Collect all canonical points: terminals + wire vertices.
    const dsu = new Map();
    const find = (k) => {
        const path = [];
        while (dsu.get(k) !== k) {
            path.push(k);
            k = dsu.get(k);
        }
        for (const p of path)
            dsu.set(p, k);
        return k;
    };
    const union = (a, b) => {
        const ra = find(a), rb = find(b);
        if (ra !== rb)
            dsu.set(ra, rb);
    };
    const seen = (k) => { if (!dsu.has(k))
        dsu.set(k, k); };
    const partTerms = state.parts.map(p => ({
        p, terminals: partTerminals(p),
    }));
    for (const { terminals } of partTerms) {
        for (const t of terminals)
            seen(`${t.pos[0]},${t.pos[1]}`);
    }
    for (const w of state.wires) {
        for (const pt of w.points)
            seen(`${pt[0]},${pt[1]}`);
        for (let i = 1; i < w.points.length; i++) {
            union(`${w.points[i - 1][0]},${w.points[i - 1][1]}`, `${w.points[i][0]},${w.points[i][1]}`);
        }
    }
    // Ground roots → node "0".
    const groundRoots = new Set();
    for (const { p } of partTerms) {
        if (p.type === 'gnd') {
            groundRoots.add(find(`${p.x},${p.y}`));
        }
    }
    // User-provided labels override auto-numbers. We collect every
    // labelled wire's root → label assignments. If two wires that share
    // a root carry conflicting labels, we keep the lexicographically
    // first so the choice is at least deterministic, and remember the
    // conflict so we can surface a warning in the netlist header.
    const rootLabel = new Map();
    const labelConflicts = [];
    const labelToRoot = new Map();
    for (const w of state.wires) {
        const lab = sanitizeNetLabel(w.label);
        if (!lab)
            continue;
        if (!w.points.length)
            continue;
        const root = find(`${w.points[0][0]},${w.points[0][1]}`);
        if (groundRoots.has(root)) {
            // A label on a wire that's also tied to ground is meaningless
            // (the node name is fixed at "0"). Skip silently.
            continue;
        }
        const existing = rootLabel.get(root);
        if (existing && existing !== lab) {
            labelConflicts.push({ root, kept: existing < lab ? existing : lab,
                dropped: existing < lab ? lab : existing });
            rootLabel.set(root, existing < lab ? existing : lab);
        }
        else if (!existing) {
            // Reject the label if another net already owns it — this would
            // break SPICE node-uniqueness.
            const other = labelToRoot.get(lab);
            if (other && other !== root) {
                labelConflicts.push({ root, kept: null, dropped: lab,
                    reason: 'duplicate' });
                continue;
            }
            rootLabel.set(root, lab);
            labelToRoot.set(lab, root);
        }
    }
    const nodeMap = new Map();
    let nextNode = 1;
    const nodeOfKey = (k) => {
        const r = find(k);
        if (groundRoots.has(r))
            return '0';
        const lab = rootLabel.get(r);
        if (lab !== undefined)
            return lab;
        if (!nodeMap.has(r))
            nodeMap.set(r, String(nextNode++));
        return nodeMap.get(r);
    };
    const nodeAt = (pt) => {
        const k = `${pt[0]},${pt[1]}`;
        if (!dsu.has(k))
            return '?';
        return nodeOfKey(k);
    };
    // Build netlist lines.
    const lines = [];
    lines.push('* sycan circuit netlist');
    lines.push(`* generated ${new Date().toISOString()}`);
    for (const c of labelConflicts) {
        if (c.reason === 'duplicate') {
            lines.push(`* warning: label "${c.dropped}" already in use; ignored`);
        }
        else {
            lines.push(`* warning: conflicting net labels on the same node: ` +
                `kept "${c.kept}", dropped "${c.dropped}"`);
        }
    }
    lines.push('');
    // Output order matches SPICE convention by prefix: V, I, R, L, C,
    // D, Q, M, X (sub-circuit), then E/F/G/H controlled sources at the
    // end. Within a prefix, alphabetical by id.
    const prefixOrder = {
        V: 0, I: 1, R: 2, L: 3, C: 4, D: 5, Q: 6, M: 7, X: 8,
        E: 9, G: 10, F: 11, H: 12,
    };
    const sorted = state.parts.slice().sort((a, b) => {
        const pa = ELEM_TYPES[a.type]?.prefix ?? 'Z';
        const pb = ELEM_TYPES[b.type]?.prefix ?? 'Z';
        return (prefixOrder[pa] ?? 99) - (prefixOrder[pb] ?? 99)
            || a.id.localeCompare(b.id);
    });
    let emitted = 0;
    for (const p of sorted) {
        const meta = ELEM_TYPES[p.type];
        if (!meta || !meta.netlist)
            continue;
        // Resolve port name → node name lookup for this part. The kind's
        // emitter calls this for each port it cares about.
        const ports = partTerminals(p);
        const portNode = (name) => {
            const t = ports.find(t => t.name === name);
            return t ? nodeOfKey(`${t.pos[0]},${t.pos[1]}`) : '?';
        };
        const line = meta.netlist(p, portNode);
        if (line == null)
            continue;
        lines.push(line);
        emitted += 1;
    }
    if (emitted === 0) {
        lines.push('* (empty — place parts to populate)');
    }
    lines.push('.end');
    // Reverse-index the union-find by node-name for the calc-node
    // highlight: walk every key in the DSU once and bucket into name →
    // Set("x,y").
    const gridPointsByNode = new Map();
    for (const k of dsu.keys()) {
        const name = nodeOfKey(k);
        let s = gridPointsByNode.get(name);
        if (!s) {
            s = new Set();
            gridPointsByNode.set(name, s);
        }
        s.add(k);
    }
    return {
        text: lines.join('\n'),
        nodeAt,
        gridPointsOfNode: (name) => gridPointsByNode.get(name) || new Set(),
    };
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
    }
    catch (_) { /* quota / private mode — silently ignore */ }
}
function loadLocal() {
    try {
        const raw = localStorage.getItem(LS_KEY);
        if (!raw)
            return false;
        const data = JSON.parse(raw);
        state.parts = data.parts || [];
        state.wires = data.wires || [];
        state.nextId = data.nextId || 1;
        state.nameCounters = data.nameCounters || {};
        if (data.pan)
            state.pan = data.pan;
        if (data.zoom)
            state.zoom = data.zoom;
        return true;
    }
    catch (_) {
        return false;
    }
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
    const target = e.target;
    const f = target.files && target.files[0];
    if (!f)
        return;
    try {
        const text = await f.text();
        const data = JSON.parse(text);
        if (!data.parts || !Array.isArray(data.parts))
            throw new Error('bad shape');
        state.parts = data.parts;
        state.wires = data.wires || [];
        state.nameCounters = data.nameCounters || {};
        state.nextId = Math.max(state.nextId, ...state.wires.map(w => parseInt(String(w.id).replace(/\D/g, '') || '0', 10) + 1));
        state.selectedIds.clear();
        pushHistory();
        refreshProps();
        render();
    }
    catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        alert('Import failed: ' + msg);
    }
    target.value = ''; // allow re-importing same file
});
document.getElementById('btn-copy').addEventListener('click', async () => {
    try {
        await navigator.clipboard.writeText(netlistEl.value);
        flashHint('Netlist copied to clipboard');
    }
    catch (_) {
        netlistEl.select();
        document.execCommand('copy');
        flashHint('Netlist copied to clipboard');
    }
});
let hintTimer = null;
function flashHint(msg) {
    hint.textContent = msg;
    if (hintTimer !== null)
        clearTimeout(hintTimer);
    hintTimer = window.setTimeout(refreshHint, 1600);
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
    // Then collapse every "wire" object so it spans end-to-end —
    // separate wires meeting collinearly with no third party at the
    // seam fuse into one polyline, and any redundant interior vertex
    // (collinear, no other wire / terminal there) gets erased.
    mergeCollinearWires();
    simplifyCollinearVertices();
    // After coalesce we know the connected components — propagate user
    // labels to every unlabelled wire in a labelled component so the
    // saved snapshot is in its canonical "named has priority" form.
    propagateLabels();
    // Any structural edit invalidates the most recent calc-node and
    // net-highlight lookups (the net they pointed at may have been
    // reorganised). Drop the highlights; the user can re-pick if they
    // still care.
    state.calcNodeHighlight = null;
    state.netHighlightOverlay = null;
    // Dedupe identical snapshots so a click-but-don't-drag mousedown→
    // mouseup pair (which still calls commitMove → pushHistory with a
    // zero-delta) doesn't pad history with no-ops. Without this, every
    // such no-op would consume one Ctrl+Z press, making the user feel
    // like undo "does nothing" before finally undoing the real edit.
    const snap = snapshot();
    if (historyIdx >= 0 && editHistory[historyIdx] === snap)
        return;
    editHistory.length = historyIdx + 1;
    editHistory.push(snap);
    if (editHistory.length > HISTORY_LIMIT)
        editHistory.shift();
    historyIdx = editHistory.length - 1;
}
function restore(idx) {
    if (idx < 0 || idx >= editHistory.length)
        return false;
    const data = JSON.parse(editHistory[idx]);
    state.parts = data.parts || [];
    state.wires = data.wires || [];
    state.nameCounters = data.nameCounters || {};
    state.nextId = data.nextId || 1;
    state.selectedIds.clear();
    state.wireDraft = null;
    state.boxSelect = null;
    state.moveDraft = null;
    state.calcNodeHighlight = null;
    state.netHighlightOverlay = null;
    state.selectedSegments.clear();
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
    if (historyIdx < editHistory.length - 1 && restore(historyIdx + 1)) {
        refreshProps();
        render();
    }
});
document.getElementById('btn-clear').addEventListener('click', () => {
    if (state.parts.length === 0 && state.wires.length === 0)
        return;
    if (!confirm('Clear all parts and wires?'))
        return;
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
        xmin = Math.min(xmin, x0);
        ymin = Math.min(ymin, y0);
        xmax = Math.max(xmax, x1);
        ymax = Math.max(ymax, y1);
    }
    for (const w of state.wires) {
        for (const [x, y] of w.points) {
            xmin = Math.min(xmin, x);
            ymin = Math.min(ymin, y);
            xmax = Math.max(xmax, x);
            ymax = Math.max(ymax, y);
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
    b.addEventListener('click', () => {
        const t = b.dataset['tool'];
        if (t)
            setTool(t);
    });
}
// Components dropdown — select switches the tool to the chosen part.
document.getElementById('part-picker')
    .addEventListener('change', (e) => {
    const v = e.target.value;
    if (v)
        setTool(v);
});
document.addEventListener('keydown', (e) => {
    // Ignore when typing in an input/textarea — let native shortcuts win
    // there (Ctrl+C / Ctrl+V on the netlist box etc.).
    const tgt = e.target;
    if (tgt && typeof tgt.matches === 'function' && tgt.matches('input, textarea')) {
        return;
    }
    const k = e.key.toLowerCase();
    // Ctrl/Cmd + letter shortcuts (handled before the bare-letter map
    // so 'c' as copy doesn't switch to the capacitor tool).
    if (e.ctrlKey || e.metaKey) {
        if (k === 'z' && !e.shiftKey) {
            document.getElementById('btn-undo').click();
            e.preventDefault();
            return;
        }
        if (k === 'y' || (k === 'z' && e.shiftKey)) {
            document.getElementById('btn-redo').click();
            e.preventDefault();
            return;
        }
        if (k === 'c') {
            copySelection(/*cut=*/ false);
            e.preventDefault();
            return;
        }
        if (k === 'x') {
            copySelection(/*cut=*/ true);
            e.preventDefault();
            return;
        }
        if (k === 'v') {
            pasteClipboard();
            e.preventDefault();
            return;
        }
        if (k === 'a') {
            state.selectedIds = new Set(state.parts.map(p => p.id));
            setTool('select');
            refreshProps();
            render();
            e.preventDefault();
            return;
        }
        return; // any other Ctrl/Cmd combo: don't intercept
    }
    // 'U' expands a segment-only selection (set by clicking or
    // box-selecting a portion of a wire in the select tool) to every
    // *wire* in the same connected component, seeded from the first
    // entry in ``selectedSegments``. Connected parts are *not* included
    // — this stays a wires-only selection so subsequent moves/deletes
    // don't drag the surrounding components along. Handled before the
    // tool-letter map so the bare `u` doesn't get diverted into a
    // part-placement shortcut.
    if (k === 'u' && !e.altKey && state.selectedSegments.size) {
        const firstKey = state.selectedSegments.values().next().value;
        const wireId = firstKey.slice(0, firstKey.indexOf('|'));
        const seed = state.wires.find(w => w.id === wireId);
        if (seed) {
            const { wireIds } = netMembers(seed);
            state.selectedIds.clear();
            state.selectedSegments.clear();
            for (const id of wireIds)
                selectWholeWire(id);
            flashHint(`Extended to net (${wireIds.size} wire${wireIds.size === 1 ? '' : 's'})`);
            refreshProps();
            render();
        }
        e.preventDefault();
        return;
    }
    // 'M' enters move mode for the current selection, tracking the
    // cursor (commits on next click; Esc cancels). Handled before the
    // single-letter tool map so it doesn't shadow the dedicated key.
    if (k === 'm' && !e.altKey) {
        if (state.selectedIds.size && !state.moveDraft) {
            startMove([...state.selectedIds], snapPt(state.cursorWorld), 
            /*viaDrag=*/ false, /*freshlyPasted=*/ false);
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
        'r': 'res', 'l': 'ind', 'c': 'cap',
        'v': 'vsrc', 'i': 'isrc',
        'd': 'diode',
        'w': 'WIRE', 'g': 'gnd',
        's': 'select', 'x': 'delete', 'b': 'rotate',
        'h': 'highlight',
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
        if (state.calcNode.armed) {
            cancelCalcNodePick();
        }
        else if (state.copyAnchorPending) {
            cancelCopyAnchor();
        }
        else if (state.moveDraft) {
            cancelMove();
        }
        else if (state.wireDraft) {
            state.wireDraft = null;
            refreshHint();
            render();
        }
        else if (state.netHighlightOverlay) {
            state.netHighlightOverlay = null;
            flashHint('Net highlight cleared');
            render();
        }
        else if (state.selectedIds.size || state.selectedSegments.size) {
            state.selectedIds.clear();
            state.selectedSegments.clear();
            refreshProps();
            render();
        }
        else {
            setTool('select');
        }
        e.preventDefault();
        return;
    }
    if ((e.key === 'Delete' || e.key === 'Backspace')
        && (state.selectedIds.size || state.selectedSegments.size)) {
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
        }
        else if (state.selectedIds.size) {
            let did = false;
            for (const id of state.selectedIds) {
                const p = state.parts.find(p => p.id === id);
                if (p) {
                    p.rot = (p.rot + 90) % 360;
                    did = true;
                }
            }
            if (did) {
                pushHistory();
                render();
            }
        }
        else if (isElemKind(state.tool)) {
            previewRot = (previewRot + 90) % 360;
            if (placementPreview)
                placementPreview.rot = previewRot;
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
    if (!state.selectedIds.size && !state.selectedSegments.size)
        return;
    // Selected parts vanish whole.
    state.parts = state.parts.filter(p => !state.selectedIds.has(p.id));
    // Wires: delete is segment-level. For each wire we ask
    // "which of its segments are marked?" and rebuild the wire from
    // the unmarked runs. A wire whose every segment is marked
    // vanishes; a wire with a chunk taken out of the middle splits
    // into two; one with a contiguous head or tail removed shrinks.
    const newWires = [];
    for (const w of state.wires) {
        const numSegs = w.points.length - 1;
        const sel = new Set(wireSelectedSegments(w.id));
        if (sel.size === 0) {
            newWires.push(w);
            continue;
        }
        if (sel.size === numSegs) {
            // Every segment selected → drop the wire entirely.
            continue;
        }
        // Walk segments, emitting one sub-wire per contiguous run of
        // *unselected* indices. Each run [runStart, runEnd] consumes
        // points[runStart .. runEnd + 1] (one more point than segments).
        let runStart = null;
        const flushRun = (runEnd) => {
            if (runStart === null)
                return;
            const points = w.points.slice(runStart, runEnd + 2);
            newWires.push({
                id: `W${state.nextId++}`,
                points,
                label: w.label,
            });
            runStart = null;
        };
        for (let i = 0; i < numSegs; i++) {
            if (sel.has(i)) {
                if (runStart !== null)
                    flushRun(i - 1);
            }
            else if (runStart === null) {
                runStart = i;
            }
        }
        if (runStart !== null)
            flushRun(numSegs - 1);
    }
    state.wires = newWires;
    state.selectedIds.clear();
    state.selectedSegments.clear();
    pushHistory();
    refreshProps();
    render();
}
// Two-step copy/cut: stash the selection ids and let the next canvas
// click decide where the anchor sits. Until that click, the clipboard
// is *not* mutated — Esc cancels and leaves the previous clipboard
// untouched.
function copySelection(cut = false) {
    if (!state.selectedIds.size)
        return;
    // Cancel any in-flight move first so the click that picks the anchor
    // doesn't accidentally commit a move.
    if (state.moveDraft)
        cancelMove();
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
    if (!cap)
        return;
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
            label: w.label,
        })),
    };
    if (cap.cut) {
        state.parts = state.parts.filter(p => !idSet.has(p.id));
        state.wires = state.wires.filter(w => !idSet.has(w.id));
        state.selectedIds.clear();
        state.selectedSegments.clear();
        pushHistory();
        refreshProps();
    }
    const np = selParts.length, nw = selWires.length;
    const frags = [];
    if (np)
        frags.push(`${np} part${np === 1 ? '' : 's'}`);
    if (nw)
        frags.push(`${nw} wire${nw === 1 ? '' : 's'}`);
    flashHint(`${cap.cut ? 'Cut' : 'Copied'} ${frags.join(' + ')} ` +
        `(anchor at ${anchor[0]}, ${anchor[1]})`);
    refreshHint();
    render();
}
function cancelCopyAnchor() {
    if (!state.copyAnchorPending)
        return;
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
    if (np + nw === 0)
        return;
    // Anchor at the current cursor (snapped). If the user hasn't put
    // the cursor over the canvas yet, anchor a couple of cells off the
    // origin so duplicates don't stack invisibly.
    let anchor = snapPt(state.cursorWorld);
    if (!anchor[0] && !anchor[1])
        anchor = [GRID * 2, GRID * 2];
    const newIds = new Set();
    for (const c of clipboard.parts || []) {
        addPart(c.type, anchor[0] + c.dx, anchor[1] + c.dy, c.rot);
        const fresh = state.parts[state.parts.length - 1];
        if (c.value && c.type !== 'gnd')
            fresh.value = c.value;
        if (c.ctrlSrc)
            fresh.ctrlSrc = c.ctrlSrc;
        newIds.add(fresh.id);
    }
    for (const c of clipboard.wires || []) {
        const id = `W${state.nextId++}`;
        // Drop the label on duplicate-paste so two wires don't claim the
        // same net name. The user can re-label one of them after placing.
        const labelClash = c.label && state.wires.some(w => w.label === c.label);
        state.wires.push({
            id,
            points: c.points.map(([x, y]) => [anchor[0] + x, anchor[1] + y]),
            ...(c.label && !labelClash ? { label: c.label } : {}),
        });
        newIds.add(id);
    }
    state.selectedIds = newIds;
    setTool('select');
    // Hand off to move-mode so the user can position the paste before
    // it's stamped down. Click to commit, Esc to throw it away.
    startMove([...newIds], anchor, /*viaDrag=*/ false, /*freshlyPasted=*/ true);
    refreshProps();
    refreshHint();
    render();
    const frags = [];
    if (np)
        frags.push(`${np} part${np === 1 ? '' : 's'}`);
    if (nw)
        frags.push(`${nw} wire${nw === 1 ? '' : 's'}`);
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
    // Snapshot pre-drag state so cancel / parity-revert can undo any
    // partial-segment splits we're about to perform. Skip for paste-
    // place moves — there's no pre-drag state to roll back to (the
    // pasted items only exist *because* of the paste).
    const preDragSnapshot = freshlyPasted ? null : {
        wires: deepCopyWires(state.wires),
        selectedIds: [...state.selectedIds],
        selectedSegments: [...state.selectedSegments],
        nextId: state.nextId,
    };
    // Segment-level drag: split any partially-selected wire into
    // pieces. The selected pieces become new wires that move; the
    // unselected pieces become new wires that stay put. After this,
    // every wire in `effectiveIds` either translates entirely or
    // doesn't appear in the move at all — same invariant the rest of
    // startMove (and updateMove) already assumed.
    const effectiveIds = freshlyPasted ? ids : splitPartialWires(ids);
    const origs = new Map();
    for (const id of effectiveIds) {
        const part = state.parts.find(p => p.id === id);
        if (part) {
            origs.set(id, { kind: 'part', x: part.x, y: part.y });
            continue;
        }
        const wire = state.wires.find(w => w.id === id);
        if (wire) {
            origs.set(id, { kind: 'wire',
                points: wire.points.map(pt => [pt[0], pt[1]]) });
        }
    }
    if (!origs.size)
        return;
    // Drag-mode capture. Off for paste-placement (a fresh paste's
    // wires aren't connected to the surrounding circuit yet, so there's
    // nothing to capture or reroute). Otherwise scan the wire list and
    // bucket each wire by where its endpoints sit relative to the
    // selected parts' terminals.
    const dragMode = !freshlyPasted &&
        document.getElementById('drag-mode')?.checked === true;
    if (dragMode) {
        // Anchor points whose original positions follow the move by
        // ``delta``: every selected part's terminal *and* every endpoint
        // of a wire in ``effectiveIds``. The latter is what lets
        // partial-segment splits auto-reroute — when ``splitPartialWires``
        // detaches a selected sub-wire from its parent, the unselected
        // remainder still has an endpoint at the split boundary, and we
        // need that boundary point to register as a moving anchor so the
        // spanning detection captures the unselected piece.
        const selectedTerminalKeys = new Set();
        for (const id of effectiveIds) {
            const p = state.parts.find(pp => pp.id === id);
            if (p) {
                for (const t of partTerminals(p)) {
                    selectedTerminalKeys.add(`${t.pos[0]},${t.pos[1]}`);
                }
                continue;
            }
            const w = state.wires.find(ww => ww.id === id);
            if (w && w.points.length >= 2) {
                const first = w.points[0];
                const last = w.points[w.points.length - 1];
                selectedTerminalKeys.add(`${first[0]},${first[1]}`);
                selectedTerminalKeys.add(`${last[0]},${last[1]}`);
            }
        }
        if (selectedTerminalKeys.size > 0) {
            for (const w of state.wires) {
                if (origs.has(w.id))
                    continue; // already explicitly selected
                if (w.points.length < 2)
                    continue;
                const first = w.points[0];
                const last = w.points[w.points.length - 1];
                const startInside = selectedTerminalKeys.has(`${first[0]},${first[1]}`);
                const endInside = selectedTerminalKeys.has(`${last[0]},${last[1]}`);
                if (startInside && endInside) {
                    origs.set(w.id, { kind: 'wire-captured',
                        points: w.points.map(pt => [pt[0], pt[1]]) });
                }
                else if (startInside !== endInside) {
                    // Capture the wire's original axis at the inside end so
                    // the reroute keeps the same first-segment orientation.
                    const insideEnd = startInside ? 'start' : 'end';
                    const a = startInside ? w.points[0] : w.points[w.points.length - 1];
                    const b = startInside ? w.points[1] : w.points[w.points.length - 2];
                    const axisHint = a[0] === b[0] ? 'v' : 'h';
                    origs.set(w.id, { kind: 'wire-spanning',
                        points: w.points.map(pt => [pt[0], pt[1]]),
                        insideEnd, axisHint });
                }
            }
        }
    }
    // Optional pre-drag connectivity snapshot for the parity check.
    const parityOn = document.getElementById('parity-check')?.checked === true;
    const paritySig = (dragMode && parityOn) ? netSignature() : null;
    state.moveDraft = {
        ids: [...effectiveIds],
        origs,
        pickup: [pickup[0], pickup[1]],
        delta: [0, 0],
        viaDrag,
        freshlyPasted,
        dragMode,
        parityCheck: parityOn,
        paritySig,
        preDragSnapshot,
    };
    wrap.classList.add('moving');
    refreshHint();
}
function updateMove(world) {
    const md = state.moveDraft;
    if (!md)
        return;
    const dx = snap(world[0] - md.pickup[0]);
    const dy = snap(world[1] - md.pickup[1]);
    if (dx === md.delta[0] && dy === md.delta[1])
        return;
    md.delta = [dx, dy];
    for (const [id, orig] of md.origs) {
        if (orig.kind === 'part') {
            const part = state.parts.find(p => p.id === id);
            if (part) {
                part.x = orig.x + dx;
                part.y = orig.y + dy;
            }
        }
        else if (orig.kind === 'wire' || orig.kind === 'wire-captured') {
            const wire = state.wires.find(w => w.id === id);
            if (wire) {
                wire.points = orig.points.map(([x, y]) => [x + dx, y + dy]);
            }
        }
        else if (orig.kind === 'wire-spanning') {
            const wire = state.wires.find(w => w.id === id);
            if (!wire)
                continue;
            // Drag-the-end semantics: keep every original vertex *except*
            // the inside endpoint, which follows the moved part by `delta`.
            // If the dragged endpoint stays Manhattan-aligned with its
            // neighbour vertex (same x or same y on the original axis),
            // we only nudge the endpoint. Otherwise we splice in one
            // corner so the segment immediately adjacent to the endpoint
            // becomes an L — the rest of the wire stays untouched. This
            // is what KiCad does and it preserves the user's hand-drawn
            // routing through the middle of long wires.
            const pts = orig.points.map(pt => [pt[0], pt[1]]);
            if (orig.insideEnd === 'end') {
                const n = pts.length;
                const prev = pts[n - 2];
                const newEnd = [pts[n - 1][0] + dx, pts[n - 1][1] + dy];
                if (orig.axisHint === 'h') {
                    if (prev[1] === newEnd[1]) {
                        pts[n - 1] = newEnd;
                    }
                    else {
                        // Keep horizontal at prev.y up to newEnd.x, then jog.
                        pts.splice(n - 1, 1, [newEnd[0], prev[1]], newEnd);
                    }
                }
                else {
                    if (prev[0] === newEnd[0]) {
                        pts[n - 1] = newEnd;
                    }
                    else {
                        pts.splice(n - 1, 1, [prev[0], newEnd[1]], newEnd);
                    }
                }
            }
            else {
                // insideEnd === 'start': mirror image at the head of the
                // polyline.
                const next = pts[1];
                const newStart = [pts[0][0] + dx, pts[0][1] + dy];
                if (orig.axisHint === 'h') {
                    if (next[1] === newStart[1]) {
                        pts[0] = newStart;
                    }
                    else {
                        pts.splice(0, 1, newStart, [newStart[0], next[1]]);
                    }
                }
                else {
                    if (next[0] === newStart[0]) {
                        pts[0] = newStart;
                    }
                    else {
                        pts.splice(0, 1, newStart, [next[0], newStart[1]]);
                    }
                }
            }
            // Drop consecutive duplicates that can appear when the new
            // endpoint lands on top of its neighbour vertex.
            const dedup = [pts[0]];
            for (let i = 1; i < pts.length; i++) {
                const cur = pts[i], last = dedup[dedup.length - 1];
                if (cur[0] !== last[0] || cur[1] !== last[1])
                    dedup.push(cur);
            }
            wire.points = dedup;
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
    if (!md)
        return;
    const ax = md.pickup[0] + md.delta[0];
    const ay = md.pickup[1] + md.delta[1];
    // 90° CW in SVG coords: (dx, dy) → (-dy, dx).
    const spin = (x, y) => [ax - (y - ay), ay + (x - ax)];
    for (const [id, _orig] of md.origs) {
        const part = state.parts.find(p => p.id === id);
        if (part) {
            const [nx, ny] = spin(part.x, part.y);
            part.x = nx;
            part.y = ny;
            part.rot = (part.rot + 90) % 360;
            continue;
        }
        const wire = state.wires.find(w => w.id === id);
        if (wire)
            wire.points = wire.points.map(([x, y]) => spin(x, y));
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
            newOrigs.set(id, { kind: 'wire',
                points: wire.points.map(pt => [pt[0], pt[1]]) });
        }
    }
    md.origs = newOrigs;
    render();
}
function commitMove() {
    if (!state.moveDraft)
        return;
    const md = state.moveDraft;
    // Zero-delta no-op: a click-without-drag (mousedown immediately
    // followed by mouseup with no movement) still routes through
    // startMove → commitMove. If startMove physically split any
    // partially-selected wires, we undo the split here so the click
    // doesn't leave phantom sub-wires the user never asked for. No
    // pushHistory either — there's nothing structural to record.
    if (md.delta[0] === 0 && md.delta[1] === 0 && md.preDragSnapshot
        && !md.freshlyPasted) {
        restorePreDragSnapshot(md);
        state.moveDraft = null;
        wrap.classList.remove('moving');
        refreshProps();
        refreshHint();
        render();
        return;
    }
    // Parity check (drag-mode only). If the drag changed the
    // connectivity partition — typically because a re-routed
    // spanning wire now passes through another terminal, or a
    // captured wire's seam coincided with an unselected wire — we
    // restore the originals and abort the commit. The user sees a
    // status-line note explaining what happened.
    if (md.dragMode && md.parityCheck && md.paritySig !== null
        && (md.delta[0] !== 0 || md.delta[1] !== 0)) {
        const post = netSignature();
        if (post !== md.paritySig) {
            // Restore from the pre-drag snapshot — startMove may have
            // physically split partially-selected wires, so the origs
            // map alone can't undo all of it.
            if (md.preDragSnapshot) {
                restorePreDragSnapshot(md);
            }
            else {
                for (const [id, orig] of md.origs) {
                    if (orig.kind === 'part') {
                        const part = state.parts.find(p => p.id === id);
                        if (part) {
                            part.x = orig.x;
                            part.y = orig.y;
                        }
                    }
                    else {
                        const wire = state.wires.find(w => w.id === id);
                        if (wire)
                            wire.points = orig.points;
                    }
                }
            }
            state.moveDraft = null;
            wrap.classList.remove('moving');
            refreshProps();
            refreshHint();
            render();
            flashHint('Drag reverted — parity check failed (connectivity would change).');
            return;
        }
    }
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
    }
    else if (md.freshlyPasted) {
        flashHint(`Placed ${md.ids.length} item${md.ids.length === 1 ? '' : 's'}`);
    }
}
function cancelMove() {
    const md = state.moveDraft;
    if (!md)
        return;
    state.moveDraft = null;
    wrap.classList.remove('moving');
    if (md.freshlyPasted) {
        // The items only existed because of the paste — wipe them out.
        const idSet = new Set(md.ids);
        state.parts = state.parts.filter(p => !idSet.has(p.id));
        state.wires = state.wires.filter(w => !idSet.has(w.id));
        state.selectedIds.clear();
        state.selectedSegments.clear();
        flashHint('Paste cancelled');
    }
    else if (md.preDragSnapshot) {
        // Restore from the pre-drag snapshot — partial-segment splits
        // create new wires and rewire the selection, none of which the
        // origs map can undo on its own.
        restorePreDragSnapshot(md);
        flashHint('Move cancelled');
    }
    else {
        for (const [id, orig] of md.origs) {
            if (orig.kind === 'part') {
                const part = state.parts.find(p => p.id === id);
                if (part) {
                    part.x = orig.x;
                    part.y = orig.y;
                }
            }
            else {
                const wire = state.wires.find(w => w.id === id);
                if (wire)
                    wire.points = orig.points;
            }
        }
        flashHint('Move cancelled');
    }
    refreshProps();
    refreshHint();
    render();
}
// ------------------------------------------------------------------
// Net Highlight tool
//
// Persistent tool: while `state.tool === 'highlight'`, every canvas
// click resolves the net under the cursor and washes every wire and
// terminal in that net with the `--highlight` colour.
//
//   * Physical reach: the connected component of the picked grid
//     point (every wire and part terminal sharing the same DSU root
//     in the netlist generator).
//   * Name-based reach: any *other* wire elsewhere in the schematic
//     whose user label matches the picked net's name. After
//     `propagateLabels` this only matters when the user has labelled
//     two physically separate components with the same name (a
//     deliberate rendezvous), but the lookup is cheap enough to keep
//     unconditionally.
//
// Clicking empty space clears the overlay; pushHistory() also drops
// it on any structural edit because the net it pointed at may have
// been split or merged.
// ------------------------------------------------------------------
function finalizeNetHighlight(snapped, world) {
    const hit = resolveNetAt(snapped, world);
    if (!hit) {
        if (state.netHighlightOverlay) {
            state.netHighlightOverlay = null;
            flashHint('Net highlight cleared');
        }
        else {
            flashHint('No net at that point — click directly on a wire or terminal.');
        }
        render();
        return;
    }
    const { node, info } = hit;
    const gridPoints = new Set(info.gridPointsOfNode(node));
    // Cross-component name propagation — any wire elsewhere whose
    // user label matches the picked net's name pulls *its* whole
    // connected component into the highlight too.
    for (const w of state.wires) {
        const lab = sanitizeNetLabel(w.label);
        if (!lab || lab !== node)
            continue;
        if (!w.points.length)
            continue;
        const wnode = info.nodeAt(w.points[0]);
        if (wnode === '?')
            continue;
        for (const k of info.gridPointsOfNode(wnode))
            gridPoints.add(k);
    }
    state.netHighlightOverlay = { node, gridPoints };
    flashHint(`Highlighting net "${node}" (${gridPoints.size} grid point${gridPoints.size === 1 ? '' : 's'})`);
    render();
}
// ------------------------------------------------------------------
// Calc Node — symbolic node-voltage solver, backed by sycan via
// pyodide.
//
// Flow:
//   1. User clicks the "Calc Node" button → arms a single-shot
//      "click a wire/terminal" pick.
//   2. The next canvas click resolves to a snapped grid point. We
//      look that point up in the netlist's union-find to find the
//      net name (label or auto-numbered).
//   3. If pyodide isn't loaded yet, kick off `loadPyodide()` and pip
//      install sympy + the sycan wheel sitting at ../repl/sycan-*.whl.
//      First run takes a few seconds; subsequent runs reuse the
//      interpreter.
//   4. Send the netlist + the chosen node name into Python, run
//      sycan.parse + solve_dc / solve_ac, and read back the symbolic
//      expression as a sympy str + LaTeX. Plain text goes into the
//      output pane immediately; if MathJax is loaded we typeset the
//      LaTeX next to it.
//
// Errors at any stage land in the same pane in red without throwing.
// ------------------------------------------------------------------
const calcStatusEl = document.getElementById('calc-status');
const calcOutputEl = document.getElementById('calc-output');
const calcBtn = document.getElementById('btn-calc-node');
const calcModeEl = document.getElementById('calc-mode');
function calcLog(msg) {
    if (calcStatusEl)
        calcStatusEl.textContent = msg;
}
function calcOutputEmpty() {
    calcOutputEl.innerHTML =
        '<div class="empty-msg">Click <em>Calc Node</em>, then click a wire ' +
            'or terminal to compute its symbolic voltage.</div>';
}
function calcOutputError(msg) {
    calcOutputEl.innerHTML = '';
    const div = document.createElement('div');
    div.className = 'calc-err';
    div.textContent = msg;
    calcOutputEl.appendChild(div);
}
calcBtn.addEventListener('click', () => {
    if (state.calcNode.armed) {
        cancelCalcNodePick();
        return;
    }
    startCalcNodePick();
});
calcModeEl.addEventListener('change', () => {
    state.calcNode.mode = calcModeEl.value;
});
state.calcNode.mode = calcModeEl.value;
function startCalcNodePick() {
    // Cancel any other interactive picker first.
    if (state.moveDraft)
        cancelMove();
    if (state.copyAnchorPending)
        cancelCopyAnchor();
    state.calcNode.armed = true;
    calcBtn.classList.add('armed');
    wrap.classList.add('picking-node');
    calcLog('Pick a net…');
    refreshHint();
    render();
}
function cancelCalcNodePick() {
    if (!state.calcNode.armed)
        return;
    state.calcNode.armed = false;
    calcBtn.classList.remove('armed');
    wrap.classList.remove('picking-node');
    calcLog('');
    refreshHint();
    render();
}
// Map a clicked grid point to a net name. Strategy:
//   1. If the snapped point is *exactly* a key in the netlist's DSU
//      (i.e. coincides with a wire vertex or a part terminal), use it.
//   2. Otherwise, find the nearest wire vertex / terminal within
//      HIT_PAD pixels of the *raw* click and use that.
//   3. Failing that, look for any wire whose segment passes through
//      the snapped point and use one of that segment's endpoints.
function resolveNetAt(snapped, world) {
    const nl = buildNetlist();
    const direct = nl.nodeAt(snapped);
    if (direct !== '?')
        return { node: direct, info: nl };
    // Nearest-vertex / terminal search by raw distance.
    let bestDist = HIT_PAD * HIT_PAD;
    let bestPt = null;
    const consider = (x, y) => {
        const dx = x - world[0], dy = y - world[1];
        const d2 = dx * dx + dy * dy;
        if (d2 <= bestDist) {
            bestDist = d2;
            bestPt = [x, y];
        }
    };
    for (const p of state.parts) {
        for (const t of partTerminals(p))
            consider(t.pos[0], t.pos[1]);
    }
    for (const w of state.wires)
        for (const pt of w.points)
            consider(pt[0], pt[1]);
    if (bestPt) {
        const node = nl.nodeAt(bestPt);
        if (node !== '?')
            return { node, info: nl };
    }
    // Walk every wire, see if any segment contains the snapped point;
    // segment endpoints are guaranteed DSU keys.
    for (const w of state.wires) {
        for (let i = 1; i < w.points.length; i++) {
            const a = w.points[i - 1], b = w.points[i];
            if (pointOnSegment(snapped, a, b)) {
                const node = nl.nodeAt(a);
                if (node !== '?')
                    return { node, info: nl };
            }
        }
    }
    return null;
}
async function finalizeCalcNodePick(snapped, world) {
    cancelCalcNodePick();
    const hit = resolveNetAt(snapped, world);
    if (!hit) {
        calcOutputError('No net at that point. Click directly on a wire or a part ' +
            'terminal.');
        return;
    }
    const { node, info } = hit;
    state.calcNodeHighlight = {
        node,
        gridPoints: info.gridPointsOfNode(node),
    };
    // Up-front output: name first, computation status second.
    calcOutputEl.innerHTML = '';
    const heading = document.createElement('div');
    heading.innerHTML = `Net <span class="calc-node-name">${escapeHtml(node)}</span>` +
        (node === '0' ? ' <em>(ground = 0)</em>' : '');
    calcOutputEl.appendChild(heading);
    if (node === '0') {
        const expr = document.createElement('div');
        expr.className = 'calc-expr';
        expr.textContent = 'V(0) = 0';
        calcOutputEl.appendChild(expr);
        render();
        return;
    }
    const exprDiv = document.createElement('div');
    exprDiv.className = 'calc-expr';
    exprDiv.textContent = 'Loading sycan…';
    calcOutputEl.appendChild(exprDiv);
    render();
    try {
        const py = await ensureSycan((s) => calcLog(s));
        const mode = pickAnalysisMode(info.text);
        calcLog(`Solving (${mode})…`);
        const result = await runSycanSolve(py, info.text, node, mode);
        if (result.error) {
            exprDiv.classList.add('calc-err');
            exprDiv.textContent = result.error;
            calcLog('Solver error');
            return;
        }
        const signature = mode === 'ac' ? `V(${node})(s) = ` : `V(${node}) = `;
        exprDiv.textContent = signature + result.expr;
        // MathJax block (best-effort; don't block on it).
        if (result.latex) {
            const mj = document.createElement('div');
            mj.className = 'calc-mathjax';
            mj.textContent =
                `$$ V_{${escapeForLatex(node)}}${mode === 'ac' ? '(s)' : ''} = ` +
                    `${result.latex} $$`;
            calcOutputEl.appendChild(mj);
            typesetCalc(mj);
        }
        calcLog(`Solved (${mode})`);
    }
    catch (err) {
        exprDiv.classList.add('calc-err');
        const msg = err instanceof Error ? err.message : String(err);
        exprDiv.textContent = msg;
        calcLog('Solver error');
    }
}
// Decide whether to ask sycan for a DC operating point or an AC
// transfer function. AC is the only mode that gives a useful symbolic
// expression for a circuit with capacitors / inductors (DC opens caps
// and shorts inductors, which collapses interesting filters to 0 V or
// V_in). The user can override via the dropdown.
function pickAnalysisMode(netlistText) {
    if (state.calcNode.mode === 'dc')
        return 'dc';
    if (state.calcNode.mode === 'ac')
        return 'ac';
    // 'auto' — peek at the netlist.
    if (/\bAC\b|^\s*[CL]\d|\bC\d/im.test(netlistText))
        return 'ac';
    // Default: DC for purely resistive nets (closed form, fast).
    return 'dc';
}
// Pyodide / sycan bootstrap. Resolves to the pyodide instance once
// sympy + sycan are installed; subsequent calls reuse the same
// promise. Status updates flow through `onStatus`.
let _pyodidePromise = null;
function ensureSycan(onStatus = () => { }) {
    if (_pyodidePromise)
        return _pyodidePromise;
    _pyodidePromise = (async () => {
        onStatus('Loading pyodide…');
        // pyodide.js script tag is loaded async — wait for it.
        let waited = 0;
        while (typeof loadPyodide !== 'function') {
            if (waited > 30000)
                throw new Error('pyodide.js failed to load');
            await new Promise(r => setTimeout(r, 100));
            waited += 100;
        }
        const py = await loadPyodide();
        onStatus('Installing sympy…');
        await py.loadPackage(['sympy', 'micropip']);
        onStatus('Installing sycan…');
        await py.runPythonAsync(`
import micropip
await micropip.install('../repl/sycan-0.1.6-py3-none-any.whl')
import sycan, sympy
print('sycan ready (sympy', sympy.__version__, ')')
`);
        onStatus('Ready');
        return py;
    })().catch((e) => {
        _pyodidePromise = null; // allow a retry on the next click
        throw e;
    });
    return _pyodidePromise;
}
// Run sycan on the given netlist + node. Returns:
//   { expr: 'sympy-style string', latex: 'LaTeX' } on success
//   { error: '...message...' }                    on failure
async function runSycanSolve(py, netlistText, nodeName, mode) {
    py.globals.set('SEDRA_NETLIST', netlistText);
    py.globals.set('SEDRA_NODE', nodeName);
    py.globals.set('SEDRA_MODE', mode);
    const result = await py.runPythonAsync(`
import json, sympy as sp
from sycan import parse
from sycan.mna import solve_dc, solve_ac

try:
    circuit = parse(SEDRA_NETLIST)
except Exception as e:
    _result = {'error': f'Parse failed: {e}'}
else:
    target = sp.Symbol(f'V({SEDRA_NODE})')
    try:
        if SEDRA_MODE == 'ac':
            sol = solve_ac(circuit)
        else:
            sol = solve_dc(circuit, simplify=True)
    except Exception as e:
        _result = {'error': f'Solver failed: {type(e).__name__}: {e}'}
    else:
        if target not in sol:
            avail = ', '.join(str(k) for k in sol)
            _result = {'error': f'Node "{SEDRA_NODE}" is not an unknown ' +
                                f'(found: {avail}). Maybe the net is tied ' +
                                f'directly to a source or to ground?'}
        else:
            expr = sol[target]
            try:
                expr_simp = sp.simplify(expr)
            except Exception:
                expr_simp = expr
            _result = {
                'expr': str(expr_simp),
                'latex': sp.latex(expr_simp),
            }
json.dumps(_result)
`);
    return JSON.parse(result);
}
function typesetCalc(el) {
    if (typeof MathJax === 'undefined' || !MathJax || !MathJax.typesetPromise)
        return;
    // The MathJax startup promise resolves when the first typeset is
    // ready. Don't await it — fire-and-forget so the plain-text result
    // is already visible while we wait.
    Promise.resolve(MathJax.startup && MathJax.startup.promise)
        .then(() => MathJax.typesetPromise([el]))
        .catch(() => { });
}
function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => {
        const map = {
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
        };
        return map[c];
    });
}
function escapeForLatex(s) {
    // Underscores are TeX's only "special" character that shows up in
    // typical SPICE node names. Wrap whole subscripts in {\\_}.
    return String(s).replace(/_/g, '\\_');
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
// ------------------------------------------------------------------
// Side-panel resize handle
//
// `#side-resizer` is a thin vertical bar between the canvas and the
// side pane. Dragging it adjusts `#side`'s width (clamped between
// the CSS min/max we declared in index.html); we re-render every
// pointermove so the canvas viewBox tracks the new flex-1 width.
// ------------------------------------------------------------------
{
    const resizer = document.getElementById('side-resizer');
    const sidePane = document.getElementById('side');
    if (resizer && sidePane) {
        let dragging = false;
        let startX = 0;
        let startWidth = 0;
        const onMove = (e) => {
            if (!dragging)
                return;
            // Drag right → narrow the side pane (it's pinned to the right
            // edge of the viewport, so a positive deltaX shrinks it).
            const dx = e.clientX - startX;
            const next = Math.max(200, Math.min(800, startWidth - dx));
            sidePane.style.width = `${next}px`;
            render();
            e.preventDefault();
        };
        const onUp = () => {
            if (!dragging)
                return;
            dragging = false;
            resizer.classList.remove('dragging');
            document.removeEventListener('mousemove', onMove);
            document.removeEventListener('mouseup', onUp);
            // Final render to settle anti-aliasing on the new width.
            render();
        };
        resizer.addEventListener('mousedown', (e) => {
            const me = e;
            if (me.button !== 0)
                return;
            dragging = true;
            startX = me.clientX;
            startWidth = sidePane.getBoundingClientRect().width;
            resizer.classList.add('dragging');
            document.addEventListener('mousemove', onMove);
            document.addEventListener('mouseup', onUp);
            me.preventDefault();
        });
    }
}
init();
