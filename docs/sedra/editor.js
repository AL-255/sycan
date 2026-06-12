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
    tool: 'select',
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
function splitWireBySegments(points, selectedSegs, selectedTerminalKeys) {
    const segCount = points.length - 1;
    if (segCount <= 0)
        return [];
    // Vertex-level "is this vertex part of the selected cluster?"
    // table. A vertex is selected via *either* incident-to-a-
    // selected-segment OR sitting-on-a-selected-device-terminal.
    // Both pathways feed the same boundary classification.
    const vtxSel = new Array(segCount + 1).fill(false);
    for (let i = 0; i < segCount; i++) {
        if (selectedSegs.has(i)) {
            vtxSel[i] = true;
            vtxSel[i + 1] = true;
        }
    }
    for (let i = 0; i <= segCount; i++) {
        if (selectedTerminalKeys.has(`${points[i][0]},${points[i][1]}`)) {
            vtxSel[i] = true;
        }
    }
    const segKindArr = [];
    for (let i = 0; i < segCount; i++) {
        if (selectedSegs.has(i)) {
            segKindArr.push('moving');
        }
        else {
            const leftSel = vtxSel[i];
            const rightSel = vtxSel[i + 1];
            if (leftSel && rightSel)
                segKindArr.push('moving'); // sandwich
            else if (leftSel || rightSel)
                segKindArr.push('boundary');
            else
                segKindArr.push('fixed');
        }
    }
    const out = [];
    let i = 0;
    while (i < segCount) {
        if (segKindArr[i] === 'boundary') {
            // Each boundary segment is its own piece — adjacent boundary
            // segments would have different inside-ends.
            out.push({
                points: points.slice(i, i + 2).map(p => [p[0], p[1]]),
                kind: 'boundary',
            });
            i++;
            continue;
        }
        const k = segKindArr[i];
        let j = i;
        while (j + 1 < segCount && segKindArr[j + 1] === k)
            j++;
        out.push({
            points: points.slice(i, j + 2).map(p => [p[0], p[1]]),
            kind: k,
        });
        i = j + 1;
    }
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
        if (w.bad)
            copy.bad = true;
        return copy;
    });
}
function splitPartialWires(ids, selectedTerminalKeys) {
    const movingIds = [];
    const boundaries = [];
    for (const id of ids) {
        const w = state.wires.find(x => x.id === id);
        if (!w) {
            // Part id (or stale wire id) — pass through unchanged.
            movingIds.push(id);
            continue;
        }
        const segCount = w.points.length - 1;
        if (segCount <= 0) {
            movingIds.push(id);
            continue;
        }
        const selSegs = new Set(wireSelectedSegments(id));
        if (selSegs.size === 0 || selSegs.size === segCount) {
            // Either nothing or everything selected — no split needed.
            movingIds.push(id);
            continue;
        }
        // Partial: split into moving / boundary / fixed pieces.
        const pieces = splitWireBySegments(w.points, selSegs, selectedTerminalKeys);
        const idx = state.wires.indexOf(w);
        if (idx === -1) {
            movingIds.push(id);
            continue;
        }
        state.selectedIds.delete(id);
        clearWireSegSel(id);
        state.wires.splice(idx, 1);
        // Vertex-level selection table reused below to decide each
        // boundary piece's inside endpoint.
        const vtxSel = new Array(segCount + 1).fill(false);
        for (let i = 0; i < segCount; i++) {
            if (selSegs.has(i)) {
                vtxSel[i] = true;
                vtxSel[i + 1] = true;
            }
        }
        for (let i = 0; i <= segCount; i++) {
            if (selectedTerminalKeys.has(`${w.points[i][0]},${w.points[i][1]}`)) {
                vtxSel[i] = true;
            }
        }
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
        // Walk the original wire's segment-index space to recover each
        // piece's `[startSegIdx, endSegIdx]` for boundary classification.
        let segCursor = 0;
        for (let pi = 0; pi < pieces.length; pi++) {
            const piece = pieces[pi];
            const pieceSegLen = piece.points.length - 1;
            const segStart = segCursor; // first segment index in the piece
            const segEnd = segCursor + pieceSegLen - 1;
            segCursor = segEnd + 1;
            const newWire = pieceWires[pi];
            if (piece.kind === 'moving') {
                state.selectedIds.add(newWire.id);
                for (let s = 0; s < newWire.points.length - 1; s++) {
                    state.selectedSegments.add(segKey(newWire.id, s));
                }
                movingIds.push(newWire.id);
            }
            else if (piece.kind === 'boundary') {
                // Boundary pieces are always size-1 (one segment): segStart
                // === segEnd. The "inside" vertex is whichever endpoint is
                // shared with the selected run.
                const leftSel = vtxSel[segStart];
                const rightSel = vtxSel[segEnd + 1];
                const insideEnd = leftSel && !rightSel ? 'start' :
                    !leftSel && rightSel ? 'end' :
                        'start'; // both-selected sandwich (degenerate, shouldn't reach here)
                const a = newWire.points[0];
                const b = newWire.points[1];
                const axisHint = a[0] === b[0] ? 'v' : 'h';
                boundaries.push({ wireId: newWire.id, insideEnd, axisHint });
            }
            // 'fixed' pieces are bystanders — no entry in either output.
        }
    }
    return { movingIds, boundaries };
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
const coords = document.getElementById('sb-coords');
const sbMode = document.getElementById('sb-mode');
const sbSel = document.getElementById('sb-sel');
const sbZoomPct = document.getElementById('sb-zoom-pct');
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
        // Stretched wires render like any other wire during a drag —
        // their live Manhattan shape is exactly what commits (KiCad
        // model), so there is no placeholder styling.
        const classes = ['wire'];
        if (w.bad)
            classes.push('wire-bad');
        el('path', { d: wirePath(w.points),
            class: classes.join(' '),
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
    const hoverPartId = (state.tool === 'select' && !state.moveDraft
        && !state.boxSelect && hoverTarget
        && hoverTarget.kind === 'part')
        ? hoverTarget.id : null;
    // Connected-terminal census (KiCad: connected pins draw nothing,
    // open pin-ends get a small outline ring). A terminal is connected
    // when a wire touches it or another terminal coincides.
    const occupied = new Map();
    for (const w of state.wires) {
        if (w.bad || w.points.length < 2)
            continue;
        for (const pt of w.points) {
            const k = `${pt[0]},${pt[1]}`;
            occupied.set(k, (occupied.get(k) || 0) + 1);
        }
        for (let i = 0; i < w.points.length - 1; i++) {
            const a = w.points[i], b = w.points[i + 1];
            if (a[0] === b[0]) {
                for (let y = Math.min(a[1], b[1]); y <= Math.max(a[1], b[1]); y += GRID) {
                    const k = `${a[0]},${y}`;
                    occupied.set(k, (occupied.get(k) || 0) + 1);
                }
            }
            else if (a[1] === b[1]) {
                for (let x = Math.min(a[0], b[0]); x <= Math.max(a[0], b[0]); x += GRID) {
                    const k = `${x},${a[1]}`;
                    occupied.set(k, (occupied.get(k) || 0) + 1);
                }
            }
        }
    }
    const termCount = new Map();
    for (const p of state.parts) {
        for (const term of partTerminals(p)) {
            const k = `${term.pos[0]},${term.pos[1]}`;
            termCount.set(k, (termCount.get(k) || 0) + 1);
        }
    }
    for (const p of state.parts) {
        const open = new Set();
        for (const term of partTerminals(p)) {
            const k = `${term.pos[0]},${term.pos[1]}`;
            if (!occupied.has(k) && (termCount.get(k) || 0) <= 1)
                open.add(term.name);
        }
        const g = drawPart(p, {
            hitParent: hitLayer,
            selected: state.selectedIds.has(p.id),
            hover: p.id === hoverPartId && !state.selectedIds.has(p.id),
            openTerminals: open,
        });
        partsLayer.appendChild(g);
    }
    // Hovered wire segment pre-selection wash.
    if (state.tool === 'select' && !state.moveDraft && !state.boxSelect
        && hoverTarget && hoverTarget.kind === 'wire'
        && hoverTarget.segIdx !== undefined) {
        const w = state.wires.find(x => x.id === hoverTarget.id);
        const i = hoverTarget.segIdx;
        if (w && i >= 0 && i < w.points.length - 1
            && !state.selectedSegments.has(segKey(w.id, i))) {
            const a = w.points[i], b = w.points[i + 1];
            el('path', {
                d: `M${a[0]},${a[1]} L${b[0]},${b[1]}`,
                class: 'wire-hover',
            }, svg);
        }
    }
    // Layer 2.5: highlight the most recently picked calc-node net.
    drawCalcNodeHighlight();
    // Layer 2.6: Net Highlight overlay (orange wash on every wire and
    // terminal that belongs to the picked net, including cross-component
    // hits that share the same user label).
    drawNetHighlight();
    // Layer 2.7: Matrix-viewer cross-link. While the user hovers a dot
    // in the MNA matrix viewer, every part whose stamp() touched that
    // cell gets a dashed blue outline so the schematic-side origin is
    // obvious at a glance.
    drawMatrixPartHighlight();
    // Layer 3: node dots at junctions where >=3 endpoints meet
    drawJunctions();
    // Layer 3.4: ERC badges (above junctions, below selection UI).
    drawErcMarkers();
    // Layer 3.45: one-shot attention halos (paste / undo / duplicate).
    drawFlashHalos();
    // Layer 3.5: live box-select rectangle. Window select (L→R) draws
    // solid select-blue; crossing select (R→L) draws dashed in the ok
    // green — KiCad's visual convention.
    if (state.boxSelect) {
        const b = state.boxSelect;
        const crossing = b.x1 < b.x0;
        const x = Math.min(b.x0, b.x1), y = Math.min(b.y0, b.y1);
        const w = Math.abs(b.x1 - b.x0), h = Math.abs(b.y1 - b.y0);
        el('rect', {
            x, y, width: w, height: h,
            fill: crossing ? 'rgba(26, 156, 84, 0.08)' : 'rgba(25, 118, 210, 0.08)',
            stroke: crossing ? 'var(--ok)' : 'var(--select)',
            'stroke-width': 1.2,
            'stroke-dasharray': crossing ? '5 4' : 'none',
            'vector-effect': 'non-scaling-stroke',
        }, svg);
    }
    // Layer 4: wire-drawing preview
    if (state.wireDraft) {
        drawWirePreview();
    }
    // Layer 4.2: connection snap indicator. While the WIRE tool is up,
    // ring the snapped cursor point green when it lands on a valid
    // connection target (part terminal or existing wire).
    if (state.tool === 'WIRE' && state.cursorInside) {
        const snapped = snapPt(state.cursorWorld);
        if (isConnectionTarget(snapped)) {
            el('circle', {
                cx: snapped[0], cy: snapped[1], r: 5 / state.zoom,
                class: 'snap-ring', 'pointer-events': 'none',
            }, svg);
        }
        if (state.wireDraft && state.wireDraft.points.length) {
            const start = state.wireDraft.points[0];
            el('circle', {
                cx: start[0], cy: start[1], r: 4 / state.zoom,
                class: 'snap-ring snap-ring-start', 'pointer-events': 'none',
            }, svg);
        }
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
    updateStatusBar();
    updateStarterCard();
    saveLocal();
}
// Live coordinate readout in the bottom-right of the canvas. While
// the user is dragging a box-select rectangle we show the
// `(x0, y0) → (x1, y1)` pair instead of the bare cursor. Hidden
// when there's no information to show (cursor off-canvas + no
// active box).
function updateCoords() {
    let txt = '';
    if (state.boxSelect) {
        const b = state.boxSelect;
        const [sx0, sy0] = snapPt([b.x0, b.y0]);
        const [sx1, sy1] = snapPt([b.x1, b.y1]);
        txt = `${sx0}, ${sy0} → ${sx1}, ${sy1}`;
    }
    else if (state.cursorInside) {
        const [cx, cy] = snapPt(state.cursorWorld);
        txt = `${cx}, ${cy}`;
    }
    // Em-dash placeholder so the zone never reads as a render failure
    // before the pointer first enters the canvas.
    coords.textContent = txt || '—, —';
}
// Selection-summary + zoom-% zones. Memoized so the per-mousemove
// render() call does zero DOM writes when nothing changed.
let sbSelLast = '';
let sbZoomLast = '';
function updateStatusBar() {
    let sel = '';
    if (state.selectedIds.size) {
        const partIds = new Set(state.parts.map(pp => pp.id));
        let np = 0, nw = 0;
        for (const id of state.selectedIds) {
            if (partIds.has(id))
                np++;
            else
                nw++;
        }
        const frags = [];
        if (np)
            frags.push(`${np} part${np === 1 ? '' : 's'}`);
        if (nw)
            frags.push(`${nw} wire${nw === 1 ? '' : 's'}`);
        sel = frags.join(', ');
    }
    if (sel !== sbSelLast) {
        sbSelLast = sel;
        sbSel.textContent = sel;
        sbSel.classList.toggle('hidden', sel === '');
    }
    const z = `${Math.round(state.zoom * 100)}%`;
    if (z !== sbZoomLast) {
        sbZoomLast = z;
        sbZoomPct.textContent = z;
    }
}
const GRID_VISIBLE_KEY = 'sycan.sedra.grid.v1';
let gridVisible = true;
try {
    gridVisible = localStorage.getItem(GRID_VISIBLE_KEY) !== '0';
}
catch (_) { /* ok */ }
function toggleGrid() {
    gridVisible = !gridVisible;
    try {
        localStorage.setItem(GRID_VISIBLE_KEY, gridVisible ? '1' : '0');
    }
    catch (_) { /* ok */ }
    render();
    flashHint(gridVisible ? 'Grid shown' : 'Grid hidden');
}
function drawGrid() {
    if (!gridVisible)
        return;
    const W = wrap.clientWidth, H = wrap.clientHeight;
    const x0 = -state.pan.x / state.zoom;
    const y0 = -state.pan.y / state.zoom;
    const x1 = x0 + W / state.zoom;
    const y1 = y0 + H / state.zoom;
    // Zoom-adaptive pitch: skip grid points so marks never pack tighter
    // than ~14 screen px (KiCad's adaptive grid). Steps double: 1,2,4,8…
    let step = GRID;
    while (step * state.zoom < 14)
        step *= 2;
    const gx0 = Math.floor(x0 / step) * step;
    const gy0 = Math.floor(y0 / step) * step;
    if ((x1 - x0) / step > 600)
        return; // hard cap
    // Constant *screen-size* marks: half-extent of the "+" is 1.25
    // screen px regardless of zoom.
    const m = 1.25 / state.zoom;
    let d = '';
    for (let y = gy0; y <= y1; y += step) {
        for (let x = gx0; x <= x1; x += step) {
            d += `M${x - m},${y} h${2 * m} M${x},${y - m} v${2 * m} `;
        }
    }
    el('path', {
        d,
        fill: 'none',
        stroke: 'var(--grid)',
        'stroke-width': 1,
        'vector-effect': 'non-scaling-stroke',
        'stroke-linecap': 'butt',
        'data-layer': 'grid',
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
                // Presentation attributes (not CSS geometry props) so the
                // rounding survives SVG export into any renderer.
                rx: 3, ry: 3,
                class: 'net-label-bg',
            });
            // Insert behind the text so the text remains legible.
            svg.insertBefore(rect, text);
        }
    }
}
let pinnedNets = [];
const PIN_HUES = [24, 142, 262, 320, 82, 198];
function pinCurrentNetHighlight() {
    const ov = state.netHighlightOverlay;
    if (!ov)
        return;
    if (pinnedNets.some(pn => pn.node === ov.node)) {
        flashHint(`${ov.node} is already pinned`);
        return;
    }
    const hue = PIN_HUES[pinnedNets.length % PIN_HUES.length];
    pinnedNets.push({ node: ov.node, gridPoints: ov.gridPoints, hue });
    state.netHighlightOverlay = null;
    updateNetLegend();
    render();
    flashHint(`Pinned net ${ov.node} — Shift+click pins more, legend chips unpin`);
}
function unpinNet(node) {
    pinnedNets = pinnedNets.filter(pn => pn.node !== node);
    updateNetLegend();
    render();
}
function updateNetLegend() {
    let legend = document.getElementById('net-legend');
    if (!pinnedNets.length) {
        legend?.remove();
        return;
    }
    if (!legend) {
        legend = document.createElement('div');
        legend.id = 'net-legend';
        wrap.appendChild(legend);
    }
    legend.innerHTML = '';
    for (const pn of pinnedNets) {
        const chip = document.createElement('button');
        chip.className = 'net-chip';
        chip.type = 'button';
        chip.title = `Unpin net ${pn.node}`;
        chip.style.setProperty('--chip-hue', String(pn.hue));
        chip.innerHTML = `<span class="net-chip-dot"></span>${pn.node} ×`;
        chip.addEventListener('click', () => unpinNet(pn.node));
        legend.appendChild(chip);
    }
}
function drawOneNetWash(set, stroke) {
    if (!set || !set.size)
        return;
    const isPt = (x, y) => set.has(`${x},${y}`);
    for (const w of state.wires) {
        // A wire belongs to the highlight if any of its vertices are in
        // the set — wires are normalised by `coalesceJunctions` so a
        // single wire never straddles two distinct nets.
        if (w.points.length && w.points.some(([x, y]) => isPt(x, y))) {
            const attrs = {
                d: wirePath(w.points),
                class: 'net-highlight',
                'vector-effect': 'non-scaling-stroke',
            };
            if (stroke)
                attrs.style = `stroke: ${stroke}`;
            el('path', attrs, svg);
        }
    }
    for (const p of state.parts) {
        for (const t of partTerminals(p)) {
            if (isPt(t.pos[0], t.pos[1])) {
                const attrs = {
                    cx: t.pos[0], cy: t.pos[1], r: 6,
                    class: 'net-highlight-term',
                };
                if (stroke)
                    attrs.style = `fill: ${stroke}`;
                el('circle', attrs, svg);
            }
        }
    }
}
function drawNetHighlight() {
    for (const pn of pinnedNets) {
        drawOneNetWash(pn.gridPoints, `hsl(${pn.hue} 70% 55%)`);
    }
    if (state.netHighlightOverlay) {
        drawOneNetWash(state.netHighlightOverlay.gridPoints, null);
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
        if (w.bad)
            continue; // bad-connection wires don't form junctions
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
            if (w.bad)
                continue; // bad wires don't contribute interest points
            for (const pt of w.points)
                points.push(pt);
        }
        let changed = false;
        for (const w of state.wires) {
            if (w.bad)
                continue; // bad wires aren't coalesced into either
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
//   * `mergeOverlappingCollinearSegments`: two separate wires whose
//     segments lie on the same axis-aligned line *and overlap* (i.e.
//     share more than just a single endpoint) collapse into a single
//     canonical interval. The lowest-indexed wire keeps the segment;
//     the others lose it. A wire that loses every segment is removed
//     from `state.wires` entirely. This handles the case where a user
//     loads / pastes a fixture with redundant duplicated wires (e.g.
//     two wires both spanning the same trunk) — without it, dragging
//     the trunk would create one ghost per duplicate.
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
function mergeOverlappingCollinearSegments() {
    // Decompose every wire into individual axis-aligned segments.
    // Skip the whole pass on the (impossible-in-practice but cheap to
    // guard) case where a polyline contains a diagonal piece — our
    // overlap geometry only makes sense on axis-aligned intervals.
    // Bad-connection wires are intentional diagonals (the auto-router
    // failed) and never participate in overlap-merging.
    const allSegs = [];
    for (let wi = 0; wi < state.wires.length; wi++) {
        const w = state.wires[wi];
        if (w.bad)
            continue;
        for (let i = 0; i < w.points.length - 1; i++) {
            const a = w.points[i], b = w.points[i + 1];
            if (a[0] === b[0] && a[1] === b[1])
                continue;
            if (a[1] === b[1]) {
                allSegs.push({
                    axis: 'h', axisVal: a[1],
                    lo: Math.min(a[0], b[0]), hi: Math.max(a[0], b[0]),
                    wireIdx: wi, wireId: w.id, origSegIdx: i,
                });
            }
            else if (a[0] === b[0]) {
                allSegs.push({
                    axis: 'v', axisVal: a[0],
                    lo: Math.min(a[1], b[1]), hi: Math.max(a[1], b[1]),
                    wireIdx: wi, wireId: w.id, origSegIdx: i,
                });
            }
            else {
                return; // diagonal — bail
            }
        }
    }
    // Group segments by (axis, axisVal). Within each group sort by `lo`
    // and merge any whose intervals strictly overlap. Touching intervals
    // (s.lo === cur.hi) do NOT merge here — those join up via
    // `mergeCollinearWires` next, which respects T-junction rules.
    const groups = new Map();
    for (const s of allSegs) {
        const k = `${s.axis}|${s.axisVal}`;
        let g = groups.get(k);
        if (!g) {
            g = [];
            groups.set(k, g);
        }
        g.push(s);
    }
    const allMerged = [];
    let anyOverlap = false;
    for (const segs of groups.values()) {
        segs.sort((x, y) => x.lo - y.lo || x.hi - y.hi);
        let cur = null;
        for (const s of segs) {
            if (cur && s.lo < cur.hi) {
                cur.hi = Math.max(cur.hi, s.hi);
                cur.contributors.push(s);
                anyOverlap = true;
            }
            else {
                if (cur)
                    allMerged.push(cur);
                cur = { axis: s.axis, axisVal: s.axisVal, lo: s.lo, hi: s.hi,
                    contributors: [s], primary: s };
            }
        }
        if (cur)
            allMerged.push(cur);
    }
    if (!anyOverlap)
        return;
    // For each merged interval, the primary contributor is the one
    // with the lowest wire-index (older wire wins, preserving its id);
    // tie-break by the segment's position in that wire's polyline.
    for (const m of allMerged) {
        let primary = m.contributors[0];
        for (const c of m.contributors) {
            if (c.wireIdx < primary.wireIdx ||
                (c.wireIdx === primary.wireIdx && c.origSegIdx < primary.origSegIdx)) {
                primary = c;
            }
        }
        m.primary = primary;
    }
    // Group surviving intervals back by their primary wire.
    const survivingByWire = new Map();
    for (const m of allMerged) {
        let arr = survivingByWire.get(m.primary.wireId);
        if (!arr) {
            arr = [];
            survivingByWire.set(m.primary.wireId, arr);
        }
        arr.push({ axis: m.axis, axisVal: m.axisVal,
            lo: m.lo, hi: m.hi, origSegIdx: m.primary.origSegIdx });
    }
    // Reassemble each wire from its surviving segments. Sort by the
    // primary's position in the original polyline so the chain follows
    // the original direction; orient each segment to match the original
    // (so a wire originally drawn right-to-left stays right-to-left).
    const newWires = [];
    const droppedIds = new Set();
    const absorbedLabel = new Map(); // wireId → label
    const same = (p, q) => p[0] === q[0] && p[1] === q[1];
    for (const w of state.wires) {
        if (w.points.length < 2) {
            newWires.push(w);
            continue;
        }
        const surviving = survivingByWire.get(w.id);
        if (!surviving || surviving.length === 0) {
            droppedIds.add(w.id);
            const lab = sanitizeNetLabel(w.label);
            if (lab)
                absorbedLabel.set(w.id, w.label);
            continue;
        }
        surviving.sort((x, y) => x.origSegIdx - y.origSegIdx);
        const points = [];
        for (const s of surviving) {
            const orig = w.points[s.origSegIdx];
            const orig2 = w.points[s.origSegIdx + 1];
            let from, to;
            if (s.axis === 'h') {
                if (orig[0] <= orig2[0]) {
                    from = [s.lo, s.axisVal];
                    to = [s.hi, s.axisVal];
                }
                else {
                    from = [s.hi, s.axisVal];
                    to = [s.lo, s.axisVal];
                }
            }
            else {
                if (orig[1] <= orig2[1]) {
                    from = [s.axisVal, s.lo];
                    to = [s.axisVal, s.hi];
                }
                else {
                    from = [s.axisVal, s.hi];
                    to = [s.axisVal, s.lo];
                }
            }
            if (points.length === 0) {
                points.push(from, to);
            }
            else {
                const tail = points[points.length - 1];
                if (same(tail, from))
                    points.push(to);
                else if (same(tail, to))
                    points.push(from);
                else
                    points.push(from, to); // discontinuity (split below)
            }
        }
        // Split the polyline at any non-axis-aligned jump (a "discontinuity"
        // from the case above, where surviving segments aren't contiguous).
        // Each disconnected piece becomes its own wire — the primary keeps
        // its id, extras get fresh ones.
        const polylines = [];
        let cur = [points[0]];
        for (let i = 1; i < points.length; i++) {
            const a = points[i - 1], b = points[i];
            if (a[0] !== b[0] && a[1] !== b[1]) {
                if (cur.length >= 2)
                    polylines.push(cur);
                cur = [b];
            }
            else {
                cur.push(b);
            }
        }
        if (cur.length >= 2)
            polylines.push(cur);
        if (polylines.length === 0) {
            droppedIds.add(w.id);
            continue;
        }
        w.points = polylines[0];
        newWires.push(w);
        for (let pi = 1; pi < polylines.length; pi++) {
            const nid = `W${state.nextId++}`;
            newWires.push({ id: nid, points: polylines[pi], label: w.label });
        }
    }
    // Hand absorbed labels down to the primary that swallowed them, but
    // only when the primary is itself unlabeled — never silently overwrite.
    // Conflicting labels stay where they were; the netlist's "conflicting
    // net labels" warning is the actionable surface for that.
    if (absorbedLabel.size > 0) {
        for (const m of allMerged) {
            const primary = newWires.find(x => x.id === m.primary.wireId);
            if (!primary || sanitizeNetLabel(primary.label))
                continue;
            for (const c of m.contributors) {
                if (c.wireId === m.primary.wireId)
                    continue;
                const lab = absorbedLabel.get(c.wireId);
                if (lab) {
                    primary.label = lab;
                    break;
                }
            }
        }
    }
    state.wires = newWires;
    for (const id of droppedIds) {
        clearWireSegSel(id);
        state.selectedIds.delete(id);
    }
    // Per-segment selection keys reference indices into the *old* polylines.
    // For any primary whose shape changed, refresh whole-wire selection.
    for (const m of allMerged) {
        if (m.contributors.length <= 1)
            continue;
        if (state.selectedIds.has(m.primary.wireId)) {
            clearWireSegSel(m.primary.wireId);
            selectWholeWire(m.primary.wireId);
        }
    }
}
function mergeCollinearWires() {
    for (let iter = 0; iter < 64; iter++) {
        // (Re)build endpoint / interior / terminal incidence each pass —
        // a successful merge mutates `state.wires`, so cached indices
        // would go stale.
        const endpoints = new Map();
        const interiorCount = new Map();
        const terminalCount = new Map();
        for (const w of state.wires) {
            if (w.bad)
                continue; // bad wires never fuse with anything
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
    // Per-grid-point per-axis segment-incidence count. Each segment of
    // axis 'h' contributes 1 to *both* its endpoints' h-count; ditto
    // for 'v'. A "real T-junction" has both horizontal AND vertical
    // incidence (or a part terminal), and we keep its vertex; a
    // collinear-only meeting (every incident segment along the same
    // axis as the host's flanking pair, no terminal) is excessive
    // visual noise and gets dropped — buildNetlist's DSU still
    // unions the resulting mid-segment crossing, so connectivity is
    // preserved even after the explicit vertex is gone.
    const hAt = new Map();
    const vAt = new Map();
    const terminalCount = new Map();
    for (const w of state.wires) {
        if (w.bad)
            continue; // bad wires don't contribute to T-counting
        for (let i = 0; i < w.points.length - 1; i++) {
            const a = w.points[i], b = w.points[i + 1];
            const axis = (a[1] === b[1]) ? 'h' : 'v';
            const map = axis === 'h' ? hAt : vAt;
            const ka = `${a[0]},${a[1]}`;
            const kb = `${b[0]},${b[1]}`;
            map.set(ka, (map.get(ka) || 0) + 1);
            map.set(kb, (map.get(kb) || 0) + 1);
        }
    }
    for (const p of state.parts) {
        for (const t of partTerminals(p)) {
            const k = `${t.pos[0]},${t.pos[1]}`;
            terminalCount.set(k, (terminalCount.get(k) || 0) + 1);
        }
    }
    for (const w of state.wires) {
        if (w.bad)
            continue;
        if (w.points.length < 3)
            continue;
        const out = [w.points[0]];
        for (let i = 1; i < w.points.length - 1; i++) {
            const prev = w.points[i - 1];
            const cur = w.points[i];
            const next = w.points[i + 1];
            const isHoriz = (prev[1] === cur[1] && cur[1] === next[1]);
            const isVert = (prev[0] === cur[0] && cur[0] === next[0]);
            if (!isHoriz && !isVert) {
                out.push(cur);
                continue;
            }
            const k = `${cur[0]},${cur[1]}`;
            // The host wire's own flanking segments contribute to its own
            // axis but never to the perpendicular axis at this vertex
            // (that would make it a corner, not a collinear interior).
            // So `perpCount` directly reflects "other wires' perpendicular
            // incidence at this point".
            const perpCount = (isHoriz ? vAt : hAt).get(k) || 0;
            const terminals = terminalCount.get(k) || 0;
            if (perpCount > 0 || terminals > 0) {
                out.push(cur); // real T-junction or terminal connection — keep.
            }
            // else: collinear-only — every incident segment runs along the
            // host's axis, so the vertex is excessive visual noise. Drop.
        }
        out.push(w.points[w.points.length - 1]);
        if (out.length !== w.points.length)
            w.points = out;
    }
}
// ------------------------------------------------------------------
// Free-endpoint tracking for drag-time dangle trimming
//
// A "free" wire endpoint is a multi-segment wire's terminal vertex
// that connects to nothing else — no other wire vertex (interior or
// endpoint) at the same grid cell, no part terminal there. Before a
// drag, we snapshot every such endpoint so that `commitMove` can tell
// the difference between:
//
//   * a dangle the user authored (pre-existing free endpoint) —
//     leave it alone, the user wants it that way.
//
//   * a dangle the auto-router created (a free endpoint that wasn't
//     free pre-drag) — trim it. This happens when a stretched
//     wire's path lands along an existing wire's line, and after
//     `mergeOverlappingCollinearSegments` consolidates the redundant
//     overlap, one wire's old endpoint is left orphaned where the
//     other wire used to terminate.
//
// Single-point "label anchor" wires (length-1 polylines) are excluded
// from both the snapshot and the trim — they have no segments to
// shorten, and label anchors are deliberate even when isolated.
// ------------------------------------------------------------------
function freeEndpointKeys() {
    const counts = new Map();
    const partTerminal = new Set();
    for (const w of state.wires) {
        if (w.bad)
            continue; // bad wires don't count as occupants
        for (const p of w.points) {
            const k = `${p[0]},${p[1]}`;
            counts.set(k, (counts.get(k) || 0) + 1);
        }
    }
    for (const p of state.parts) {
        for (const t of partTerminals(p)) {
            partTerminal.add(`${t.pos[0]},${t.pos[1]}`);
        }
    }
    const result = new Set();
    for (const w of state.wires) {
        if (w.bad)
            continue;
        if (w.points.length < 2)
            continue;
        for (const idx of [0, w.points.length - 1]) {
            const p = w.points[idx];
            const k = `${p[0]},${p[1]}`;
            if (partTerminal.has(k))
                continue;
            if ((counts.get(k) || 0) > 1)
                continue;
            result.add(k);
        }
    }
    return result;
}
// Trim wires whose endpoints became free *because* of the operation
// just committed. `preFree` is the snapshot of free-endpoint grid
// keys taken at startMove time; any current free endpoint NOT in
// that set is the trim target. Iterates because trimming a vertex
// can expose another collinear interior as the new endpoint.
function trimNewDangles(preFree) {
    for (let pass = 0; pass < 32; pass++) {
        const counts = new Map();
        const partTerminal = new Set();
        for (const w of state.wires) {
            if (w.bad)
                continue;
            for (const p of w.points) {
                const k = `${p[0]},${p[1]}`;
                counts.set(k, (counts.get(k) || 0) + 1);
            }
        }
        for (const p of state.parts) {
            for (const t of partTerminals(p)) {
                partTerminal.add(`${t.pos[0]},${t.pos[1]}`);
            }
        }
        const isNewFree = (p) => {
            const k = `${p[0]},${p[1]}`;
            if (partTerminal.has(k))
                return false;
            if ((counts.get(k) || 0) !== 1)
                return false;
            if (preFree.has(k))
                return false;
            return true;
        };
        let changed = false;
        const toDelete = new Set();
        for (const w of state.wires) {
            if (w.bad)
                continue; // never trim a bad wire — user's job
            if (w.points.length < 2)
                continue;
            // A length-3+ wire's first/last vertex can be popped without
            // dropping the wire. Single trim per wire per pass — counts is
            // a snapshot, so multi-trim within one pass would race against
            // its own state.
            if (w.points.length >= 3 && isNewFree(w.points[0])) {
                w.points.shift();
                changed = true;
                continue;
            }
            if (w.points.length >= 3 && isNewFree(w.points[w.points.length - 1])) {
                w.points.pop();
                changed = true;
                continue;
            }
            // A length-2 wire with a freshly-free endpoint is itself the
            // dangle — trim the whole wire.
            if (w.points.length === 2 &&
                (isNewFree(w.points[0]) || isNewFree(w.points[1]))) {
                toDelete.add(w.id);
                changed = true;
            }
        }
        if (toDelete.size > 0) {
            state.wires = state.wires.filter(w => !toDelete.has(w.id));
            for (const id of toDelete) {
                clearWireSegSel(id);
                state.selectedIds.delete(id);
            }
        }
        if (!changed)
            return;
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
        if (w.bad) {
            // Bad wires keep their endpoints' net connection — the user
            // will re-route the visual placeholder into a real Manhattan
            // path later, but the netlist must stay correct in the
            // meantime. Union start↔end without contributing to interior
            // / mid-segment crossings (the diagonal body has no meaningful
            // interior).
            if (w.points.length >= 2) {
                const a = w.points[0];
                const b = w.points[w.points.length - 1];
                const ka = `${a[0]},${a[1]}`;
                const kb = `${b[0]},${b[1]}`;
                seen(ka);
                seen(kb);
                union(ka, kb);
            }
            continue;
        }
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
// drag signatures catches stray T-joints and moved geometry that
// landed on a previously-unrelated net.
//
// (The end-of-wire bend itself happens inline in `updateMove`'s
// `wire-stretch` branch — we keep the original middle vertices and
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
// Drag-mode wire stretching (KiCad model)
//
// Replaces the old commit-time auto-router. KiCad's schematic move
// tool (eeschema/tools/sch_move_tool.cpp) never re-routes attached
// wires: each unselected line touching a moving connection point is
// flagged at that endpoint (STARTPOINT / ENDPOINT) and only the
// flagged end translates, with orthoLineDrag() inserting bend
// segments so the wire stays orthogonal. Connectivity is preserved
// *by construction* — there is nothing to retry and nothing to
// revert at commit time.
//
// SEDRA's polyline wires make the per-frame restatement simpler than
// KiCad's incremental bend bookkeeping: the stretched shape is
// recomputed from the wire's pre-drag points on every update, so
// there is no live cache to corrupt mid-drag.
// ------------------------------------------------------------------
// Orthogonal endpoint stretch. The moving endpoint translates by the
// drag delta; if that breaks colinearity with the first anchored
// vertex, one bend vertex restores a Manhattan path. The first-leg
// orientation keeps the wire's original axis; degenerate first
// segments (drag-spawned zero-length stubs) fall back to `axisHint`,
// then to the dominant drag direction.
function stretchWirePoints(orig, insideEnd, dx, dy, axisHint) {
    const pts = orig.map(pt => [pt[0], pt[1]]);
    if (insideEnd === 'end')
        pts.reverse();
    const E = pts[0];
    const Ep = [E[0] + dx, E[1] + dy];
    const rest = pts.slice(1);
    let out;
    if (!rest.length) {
        out = [Ep];
    }
    else {
        const P1 = rest[0];
        if (Ep[0] === P1[0] || Ep[1] === P1[1]) {
            // Still axis-aligned with the first anchored vertex — no bend.
            out = [Ep, ...rest];
        }
        else {
            let axis;
            if (E[1] === P1[1] && E[0] !== P1[0])
                axis = 'h';
            else if (E[0] === P1[0] && E[1] !== P1[1])
                axis = 'v';
            else
                axis = axisHint ?? (Math.abs(dx) >= Math.abs(dy) ? 'h' : 'v');
            const bend = axis === 'h' ? [P1[0], Ep[1]] : [Ep[0], P1[1]];
            out = [Ep, bend, ...rest];
        }
    }
    // Drop consecutive duplicates (moving end dragged back onto the
    // first anchored vertex), keeping at least two points so the wire
    // stays renderable; zero-length leftovers are removed on commit.
    const dedup = [];
    for (const pt of out) {
        const prev = dedup[dedup.length - 1];
        if (prev && prev[0] === pt[0] && prev[1] === pt[1])
            continue;
        dedup.push(pt);
    }
    while (dedup.length < 2)
        dedup.push([dedup[0][0], dedup[0][1]]);
    if (insideEnd === 'end')
        dedup.reverse();
    return dedup;
}
// Remove wires the stretch collapsed to a single grid point (every
// vertex coincident). KiCad's schematic cleanup deletes zero-length
// lines the same way. Single-point label-anchor wires (length-1
// polylines) are deliberate and not touched.
function dropZeroLengthWires() {
    const dead = new Set();
    for (const w of state.wires) {
        if (w.points.length < 2)
            continue;
        const [x0, y0] = w.points[0];
        if (w.points.every(pt => pt[0] === x0 && pt[1] === y0))
            dead.add(w.id);
    }
    if (!dead.size)
        return;
    state.wires = state.wires.filter(w => !dead.has(w.id));
    for (const id of dead) {
        state.selectedIds.delete(id);
        clearWireSegSel(id);
    }
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
        if (w.bad)
            continue;
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
        if (w.bad)
            continue;
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
    let mode;
    let h;
    if (state.copyAnchorPending) {
        mode = state.copyAnchorPending.cut ? 'Cut' : 'Copy';
        h = 'Click to pick the anchor point · <kbd>Esc</kbd> cancel';
    }
    else if (state.calcNode.armed) {
        mode = 'Calc Node';
        h = 'Click a wire or terminal to solve its symbolic voltage · <kbd>Esc</kbd> cancel';
    }
    else if (state.moveDraft) {
        mode = state.moveDraft.freshlyPasted ? 'Paste' : 'Move';
        h = state.moveDraft.viaDrag && !state.moveDraft.freshlyPasted
            ? 'Release to drop · <kbd>Esc</kbd> cancel'
            : 'Click to drop · <kbd>Esc</kbd> cancel';
    }
    else if (state.wireDraft) {
        mode = 'Wire';
        h = 'Click adds a corner · <kbd>dbl-click</kbd> finishes · <kbd>Esc</kbd> cancel';
    }
    else if (t === 'select') {
        mode = 'Select';
        h = 'Click picks · drag moves · box multi-selects · ' +
            '<kbd>U</kbd> net · <kbd>Ctrl+D</kbd> duplicate · ' +
            '<kbd>F2</kbd> edit value · <kbd>?</kbd> all shortcuts';
    }
    else if (t === 'delete') {
        mode = 'Delete';
        h = 'Click a part or wire to remove it';
    }
    else if (t === 'rotate') {
        mode = 'Rotate';
        h = 'Click a part to rotate 90°';
    }
    else if (t === 'highlight') {
        mode = 'Highlight';
        h = 'Click a wire or terminal to wash its net · click empty space to clear';
    }
    else if (t === 'WIRE') {
        mode = 'Wire';
        h = 'Click to start · each click adds a Manhattan corner · <kbd>dbl-click</kbd> finishes';
    }
    else if (isElemKind(t)) {
        mode = `Place ${ELEM_TYPES[t].prefix}`;
        h = 'Click the grid to place · <kbd>Space</kbd> rotates the ghost';
    }
    else {
        mode = '';
        h = '';
    }
    sbMode.textContent = mode;
    // A live flash owns the hint zone; the persistent hint is
    // recomputed from state when the flash timer expires.
    if (hintTimer === null)
        hint.innerHTML = h;
}
// ------------------------------------------------------------------
// Mouse handling
// ------------------------------------------------------------------
let panning = false;
let panStart = null;
// Pre-selection hover feedback (select tool): what's under the cursor
// right now. segIdx is set for wire hits.
let hoverTarget = null;
// Track whether the current mouse-down→up sequence performed a drag,
// so the synthesised click event can be suppressed for box-selects.
let suppressNextClick = false;
// ------------------------------------------------------------------
// Select-tool gesture state machine (modeled on KiCad's selection
// tool): one owner for the whole left-button press → release
// lifecycle, instead of logic scattered across mousedown / mousemove
// / mouseup / click with cross-handler flags.
//
//   idle ──mousedown──▶ pressed ──≥ threshold──▶ move | marquee
//                          │                          │
//                          └──mouseup──▶ click        └──mouseup──▶ apply
//
// * `pressed` records what was under the cursor and the modifiers at
//   press time, but mutates **nothing** — no selection change, no
//   move-draft, no wire splitting. A press only becomes a drag after
//   the cursor travels `DRAG_THRESHOLD_PX` **screen** pixels
//   (zoom-independent, like KiCad's drag threshold); otherwise the
//   release is a click and the selection semantics are applied in
//   exactly one place (`applyClickSelection`).
// * `move` wraps the existing move engine (`startMove` /
//   `updateMove` / `commitMove`): the engine — and its eager
//   partial-wire splitting — is only engaged once a real drag is
//   underway, never for plain clicks.
// * `marquee` owns `state.boxSelect`; the box is applied on release
//   (`applyMarqueeSelection`), with no minimum-size heuristics —
//   by construction a marquee only exists past the drag threshold.
//
// While a gesture is active its mousemove / mouseup handlers live on
// `window`, so releasing the button outside the canvas still
// finishes the gesture instead of stranding a half-done drag.
// ------------------------------------------------------------------
const DRAG_THRESHOLD_PX = 5;
let selectGesture = null;
function segmentTargetAt(world, wireId) {
    const w = state.wires.find(x => x.id === wireId);
    if (!w || w.points.length < 2)
        return null;
    const idx = closestSegmentIndex(world, w);
    return { wireId, key: segKey(wireId, idx), idx };
}
function beginSelectGesture(e) {
    const world = eventToWorld(e);
    const hit = pickAt(world);
    const seg = hit && hit.kind === 'wire'
        ? segmentTargetAt(world, hit.id)
        : null;
    selectGesture = {
        phase: 'pressed',
        startClient: { x: e.clientX, y: e.clientY },
        startWorld: world,
        target: { hit, seg },
        mod: e.shiftKey ? 'add'
            : (e.ctrlKey || e.metaKey) ? 'toggle'
                : 'none',
    };
    window.addEventListener('mousemove', onSelectGestureMove);
    window.addEventListener('mouseup', onSelectGestureUp);
}
function endSelectGesture() {
    selectGesture = null;
    window.removeEventListener('mousemove', onSelectGestureMove);
    window.removeEventListener('mouseup', onSelectGestureUp);
}
// Cancel an in-flight gesture without applying it: Esc mid-drag,
// pinch-zoom stealing the pointer, etc.
function abortSelectGesture() {
    if (!selectGesture)
        return;
    const phase = selectGesture.phase;
    endSelectGesture();
    if (phase === 'move' && state.moveDraft) {
        cancelMove();
    }
    else if (phase === 'marquee') {
        state.boxSelect = null;
        render();
    }
}
// Is the pressed target already part of the selection? Segment hits
// ask at segment granularity, parts at id granularity.
function pressTargetSelected(t) {
    if (!t.hit)
        return false;
    return t.seg
        ? state.selectedSegments.has(t.seg.key)
        : state.selectedIds.has(t.hit.id);
}
function addPressTarget(t) {
    if (!t.hit)
        return;
    if (t.seg) {
        state.selectedIds.add(t.seg.wireId);
        state.selectedSegments.add(t.seg.key);
    }
    else {
        state.selectedIds.add(t.hit.id);
    }
}
// Click semantics — the single place they're defined:
//   plain  → replace the selection with the target (empty → clear)
//   shift  → add, never remove (idempotent)
//   ctrl   → toggle membership
//   modifier + empty space → no-op (doesn't nuke the selection)
function applyClickSelection(g) {
    const { target, mod } = g;
    if (!target.hit) {
        if (mod === 'none' &&
            (state.selectedIds.size || state.selectedSegments.size)) {
            state.selectedIds.clear();
            state.selectedSegments.clear();
            refreshProps();
            render();
        }
        return;
    }
    if (mod === 'add') {
        addPressTarget(target);
    }
    else if (mod === 'toggle') {
        if (target.seg) {
            if (state.selectedSegments.has(target.seg.key)) {
                state.selectedSegments.delete(target.seg.key);
                if (!wireHasSegSel(target.seg.wireId)) {
                    state.selectedIds.delete(target.seg.wireId);
                }
            }
            else {
                addPressTarget(target);
            }
        }
        else if (state.selectedIds.has(target.hit.id)) {
            state.selectedIds.delete(target.hit.id);
        }
        else {
            state.selectedIds.add(target.hit.id);
        }
    }
    else {
        // Plain click: the selection becomes exactly the target — also
        // when the target was a member of a larger multi-selection
        // (clicking narrows; dragging is how you move the group).
        state.selectedIds.clear();
        state.selectedSegments.clear();
        addPressTarget(target);
    }
    refreshProps();
    render();
}
// Drag started on an item: make sure that item is part of the
// selection the move engine is about to pick up. An unselected item
// under a plain press becomes the sole selection (KiCad: dragging an
// unselected item selects it first); under a modifier press it's
// added so the drag carries the existing selection along too.
function ensureDragSelection(g) {
    if (pressTargetSelected(g.target))
        return;
    if (g.mod === 'none') {
        state.selectedIds.clear();
        state.selectedSegments.clear();
    }
    addPressTarget(g.target);
    refreshProps();
}
// Apply the finished marquee with KiCad's window/crossing semantics:
// dragging left→right selects what the box fully CONTAINS (window);
// right→left selects everything the box TOUCHES (crossing). Parts
// select by bbox (centre containment for window, bbox intersection
// for crossing); wires at segment granularity. Non-additive marquees
// replace.
function applyMarqueeSelection() {
    const b = state.boxSelect;
    if (!b)
        return;
    const crossing = b.x1 < b.x0;
    const x0 = Math.min(b.x0, b.x1), y0 = Math.min(b.y0, b.y1);
    const x1 = Math.max(b.x0, b.x1), y1 = Math.max(b.y0, b.y1);
    if (!b.additive) {
        state.selectedIds.clear();
        state.selectedSegments.clear();
    }
    for (const p of state.parts) {
        const [bx0, by0, bx1, by1] = partBBox(p);
        const hit = crossing
            ? bx0 <= x1 && bx1 >= x0 && by0 <= y1 && by1 >= y0 // bbox overlap
            : (bx0 + bx1) / 2 >= x0 && (bx0 + bx1) / 2 <= x1 // centre inside
                && (by0 + by1) / 2 >= y0 && (by0 + by1) / 2 <= y1;
        if (hit)
            state.selectedIds.add(p.id);
    }
    const inside = ([x, y]) => x >= x0 && x <= x1 && y >= y0 && y <= y1;
    // Axis-aligned segment vs rect intersection (for crossing mode).
    const segHits = (a, c) => {
        const sx0 = Math.min(a[0], c[0]), sx1 = Math.max(a[0], c[0]);
        const sy0 = Math.min(a[1], c[1]), sy1 = Math.max(a[1], c[1]);
        return sx0 <= x1 && sx1 >= x0 && sy0 <= y1 && sy1 >= y0;
    };
    for (const w of state.wires) {
        let pickedAny = false;
        for (let i = 0; i < w.points.length - 1; i++) {
            const hit = crossing
                ? segHits(w.points[i], w.points[i + 1])
                : inside(w.points[i]) && inside(w.points[i + 1]);
            if (hit) {
                state.selectedSegments.add(segKey(w.id, i));
                pickedAny = true;
            }
        }
        if (pickedAny)
            state.selectedIds.add(w.id);
    }
    refreshProps();
}
function onSelectGestureMove(e) {
    const g = selectGesture;
    if (!g)
        return;
    const world = eventToWorld(e);
    if (g.phase === 'pressed') {
        const dist = Math.hypot(e.clientX - g.startClient.x, e.clientY - g.startClient.y);
        if (dist < DRAG_THRESHOLD_PX)
            return;
        if (g.target.hit) {
            // Promote to a move drag. Only now does the move engine run —
            // including its partial-wire splitting and pre-drag snapshot.
            ensureDragSelection(g);
            startMove([...state.selectedIds], snapPt(g.startWorld), 
            /*viaDrag=*/ true, /*freshlyPasted=*/ false);
            selectGesture = state.moveDraft ? { phase: 'move' } : null;
            if (!selectGesture) {
                endSelectGesture();
                return;
            }
        }
        else {
            state.boxSelect = {
                x0: g.startWorld[0], y0: g.startWorld[1],
                x1: world[0], y1: world[1],
                additive: g.mod !== 'none',
            };
            selectGesture = { phase: 'marquee' };
        }
    }
    if (selectGesture.phase === 'move') {
        if (state.moveDraft)
            updateMove(world);
        return;
    }
    if (selectGesture.phase === 'marquee' && state.boxSelect) {
        state.boxSelect.x1 = world[0];
        state.boxSelect.y1 = world[1];
        render();
    }
}
// Self-detected double-click. The gesture's mousedown preventDefault
// stops Chrome from synthesising click/dblclick events for the select
// tool, so — like KiCad's tool framework — we detect the second
// quick click on the same part ourselves.
let lastGestureClick = null;
function onSelectGestureUp(e) {
    if (e.button !== 0)
        return;
    const g = selectGesture;
    if (!g) {
        endSelectGesture();
        return;
    }
    if (g.phase === 'pressed') {
        applyClickSelection(g);
        const partId = g.target.hit && g.target.hit.kind === 'part'
            ? g.target.hit.id : null;
        const now = Date.now();
        if (partId && lastGestureClick
            && lastGestureClick.partId === partId
            && now - lastGestureClick.t < 400
            && Math.abs(e.clientX - lastGestureClick.x) < 5
            && Math.abs(e.clientY - lastGestureClick.y) < 5) {
            // Double-click on a part: edit its value in place.
            lastGestureClick = null;
            openInlineValueEditor(partId);
        }
        else {
            lastGestureClick = partId
                ? { t: now, x: e.clientX, y: e.clientY, partId }
                : null;
        }
    }
    else if (g.phase === 'move') {
        // Esc may have cancelled the move mid-drag; commit only if the
        // draft is still live.
        if (state.moveDraft && state.moveDraft.viaDrag)
            commitMove();
    }
    else {
        applyMarqueeSelection();
        state.boxSelect = null;
        render();
    }
    endSelectGesture();
    // The browser may still synthesise a click on the canvas after
    // this mouseup; the gesture has fully handled the interaction, so
    // the click handler must treat it as already consumed.
    suppressNextClick = true;
}
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
    // Middle / right → pan. (Shift+left used to pan too, but that
    // conflicts with the selection-additive convention; shift+left is
    // now reserved for "add to selection".)
    if (e.button === 1 || e.button === 2) {
        e.preventDefault();
        if (e.button === 2) {
            rightPress = { x: e.clientX, y: e.clientY,
                px: state.pan.x, py: state.pan.y };
        }
        panning = true;
        panStart = { x: e.clientX, y: e.clientY,
            px: state.pan.x, py: state.pan.y };
        wrap.classList.add('panning');
        return;
    }
    // Left-press in select tool: hand the whole press → release
    // lifecycle to the gesture state machine. Nothing is selected,
    // moved, or split here — the press only *records* what's under
    // the cursor; mouseup (click) or crossing the drag threshold
    // (move / marquee) decides what it means.
    if (e.button === 0 && state.tool === 'select' &&
        !state.moveDraft && !selectGesture) {
        beginSelectGesture(e);
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
    // An active select gesture owns the pointer — its window-level
    // mousemove handler (which fires after this one) does the work.
    if (selectGesture)
        return;
    // Move-mode in progress (M-key / paste placement) — rubber-band
    // every selected item by the delta between pickup and cursor.
    if (state.moveDraft) {
        updateMove(world);
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
    // Hover pre-selection feedback in the select tool: light up the
    // part / wire segment a click would pick.
    if (state.tool === 'select') {
        const h = pickAt(world);
        if (!h) {
            hoverTarget = null;
        }
        else if (h.kind === 'wire') {
            const w = state.wires.find(x => x.id === h.id);
            const segIdx = w ? closestSegmentIndex(world, w) : undefined;
            hoverTarget = { kind: 'wire', id: h.id, segIdx };
        }
        else {
            hoverTarget = { kind: 'part', id: h.id };
        }
    }
    else if (hoverTarget) {
        hoverTarget = null;
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
        if (e.button === 2)
            maybeOpenContextMenu(e);
        return;
    }
    // Select-tool gestures (click / move-drag / marquee) finish in the
    // gesture's own window-level mouseup handler; M-key and paste
    // moves (viaDrag=false) commit on the next click instead. Nothing
    // to do here for either.
});
wrap.addEventListener('mouseleave', () => {
    // Drop both the placement-preview ghost and the grid-snap crosshair
    // when the cursor leaves the canvas, so neither lingers at the last
    // recorded position.
    state.cursorInside = false;
    hoverTarget = null;
    if (placementPreview)
        placementPreview = null;
    render();
});
// Right-click: context menu on a press, pan on a drag (≥4px). The
// browser's contextmenu event is only used to suppress the native
// menu — its timing differs per platform (Linux fires it on
// mousedown), so the menu itself opens from our own mouseup logic.
let rightPress = null;
wrap.addEventListener('contextmenu', (e) => e.preventDefault());
function maybeOpenContextMenu(e) {
    const press = rightPress;
    rightPress = null;
    if (!press)
        return;
    const moved = Math.hypot(e.clientX - press.x, e.clientY - press.y);
    if (moved >= 4)
        return; // right-drag pan
    // Undo the sub-threshold pan jitter.
    state.pan.x = press.px;
    state.pan.y = press.py;
    render();
    // Modal interactions own the pointer — no menu while they're live.
    if (state.moveDraft || state.wireDraft || state.copyAnchorPending
        || state.calcNode.armed || selectGesture || state.boxSelect)
        return;
    openContextMenu(e.clientX, e.clientY);
}
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
        case 'select':
            // Fully handled by the gesture state machine on mouseup
            // (`applyClickSelection` / `applyMarqueeSelection`); the
            // browser-synthesised click that follows is suppressed via
            // `suppressNextClick`, so this arm is unreachable in practice
            // and intentionally empty.
            break;
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
            if (e.shiftKey && state.netHighlightOverlay)
                pinCurrentNetHighlight();
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
const ZOOM_MIN = 0.2;
const ZOOM_MAX = 4;
const ZOOM_STEP = Math.SQRT2;
// Set absolute zoom, anchored at screen point (ax, ay) in canvas-wrap
// coords; defaults to the canvas center (keyboard / button zoom).
function setZoom(newZoom, ax, ay) {
    const z = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, newZoom));
    if (z === state.zoom)
        return;
    const cx = ax !== undefined ? ax : wrap.clientWidth / 2;
    const cy = ay !== undefined ? ay : wrap.clientHeight / 2;
    state.pan.x = cx - (cx - state.pan.x) * (z / state.zoom);
    state.pan.y = cy - (cy - state.pan.y) * (z / state.zoom);
    state.zoom = z;
    render();
}
// Trackpad-correct wheel handling (Figma convention): pinch gestures
// (delivered as ctrl+wheel) and explicit Ctrl/Cmd+wheel zoom, anchored
// at the cursor; plain wheel / two-finger scroll pans. Shift turns a
// vertical mouse wheel into horizontal pan.
wrap.addEventListener('wheel', (e) => {
    e.preventDefault();
    if (e.ctrlKey || e.metaKey) {
        // Normalise: pinch deltas are small and smooth, mouse detents are
        // ±100-ish — clamp so one detent is a pleasant ~1.6× step.
        const dy = Math.max(-40, Math.min(40, e.deltaY));
        const rect = wrap.getBoundingClientRect();
        setZoom(state.zoom * Math.exp(-dy * 0.012), e.clientX - rect.left, e.clientY - rect.top);
        return;
    }
    const horizontal = e.shiftKey && e.deltaX === 0;
    state.pan.x -= horizontal ? e.deltaY : e.deltaX;
    state.pan.y -= horizontal ? 0 : e.deltaY;
    render();
}, { passive: false });
// ------------------------------------------------------------------
// Touch gestures (mobile / tablet)
//
// Single-finger touches deliberately fall through to the browser's
// synthesized mouse events (mousedown / mousemove / mouseup), which the
// existing handlers above already cover — so a single tap places a
// part, single-finger drag draws a box-select / moves a selection, and
// so on.
//
// Two-finger touches activate a pinch-zoom + pan gesture: we track the
// two contact points' distance for zoom and their centroid for pan,
// anchored so the world point under the centroid stays fixed (same
// model as the wheel zoom anchor). preventDefault on multi-touch
// keeps the browser from synthesising a phantom mouse event for the
// second finger and from triggering its own pinch-zoom behaviour.
// `touch-action: none` on #canvas-wrap also blocks browser-native
// pan/zoom, which is required for the gesture to feel responsive
// (otherwise the browser fights us for control).
// ------------------------------------------------------------------
let pinch = null;
function touchCentroid(touches) {
    const rect = wrap.getBoundingClientRect();
    let x = 0, y = 0;
    for (let i = 0; i < touches.length; i++) {
        x += touches[i].clientX - rect.left;
        y += touches[i].clientY - rect.top;
    }
    return { x: x / touches.length, y: y / touches.length };
}
function touchDistance(touches) {
    // We only ever pinch with the first two contacts. Adding a third
    // finger keeps the pinch alive against the original pair.
    const t0 = touches[0], t1 = touches[1];
    return Math.hypot(t0.clientX - t1.clientX, t0.clientY - t1.clientY);
}
wrap.addEventListener('touchstart', (e) => {
    if (e.touches.length >= 2) {
        // Cancel any in-flight single-finger interaction that the
        // synthesized mouse events kicked off before the second finger
        // landed. Without this the gesture / moveDraft / boxSelect /
        // panning state would be left dangling because no mouseup is
        // fired once we preventDefault below.
        abortSelectGesture();
        if (state.moveDraft)
            cancelMove();
        state.boxSelect = null;
        if (panning) {
            panning = false;
            panStart = null;
            wrap.classList.remove('panning');
        }
        const c = touchCentroid(e.touches);
        pinch = {
            startDist: touchDistance(e.touches),
            startCx: c.x,
            startCy: c.y,
            startZoom: state.zoom,
            startPanX: state.pan.x,
            startPanY: state.pan.y,
        };
        e.preventDefault();
    }
}, { passive: false });
wrap.addEventListener('touchmove', (e) => {
    if (pinch && e.touches.length >= 2) {
        const dist = touchDistance(e.touches);
        if (pinch.startDist === 0)
            return;
        const rawZoom = pinch.startZoom * (dist / pinch.startDist);
        const newZoom = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, rawZoom));
        const c = touchCentroid(e.touches);
        // Two contributions to the new pan:
        //   1. zoom anchored at the gesture's *initial* centroid (keeps
        //      the world point originally pinched fixed under that
        //      screen position),
        //   2. translation by how far the centroid has moved since
        //      gesture start (two-finger pan).
        const ratio = newZoom / pinch.startZoom;
        state.pan.x = pinch.startCx - (pinch.startCx - pinch.startPanX) * ratio + (c.x - pinch.startCx);
        state.pan.y = pinch.startCy - (pinch.startCy - pinch.startPanY) * ratio + (c.y - pinch.startCy);
        state.zoom = newZoom;
        render();
        e.preventDefault();
    }
}, { passive: false });
function endPinch(e) {
    if (pinch && e.touches.length < 2) {
        pinch = null;
        // Don't try to "demote" the gesture into a single-finger drag —
        // the browser won't synthesise a mousedown for the remaining
        // contact (mouse-event synthesis only happens on the *first*
        // touch of a new sequence). The user lifts and re-taps to start
        // a new interaction.
    }
}
wrap.addEventListener('touchend', endPinch);
wrap.addEventListener('touchcancel', endPinch);
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
        if (w.bad) {
            // Bad wires keep their endpoints' net connection — the user
            // will re-route the visual placeholder into a real Manhattan
            // path later, but the netlist must stay correct in the
            // meantime. Union start↔end without contributing to interior
            // / mid-segment crossings (the diagonal body has no meaningful
            // interior).
            if (w.points.length >= 2) {
                const a = w.points[0];
                const b = w.points[w.points.length - 1];
                const ka = `${a[0]},${a[1]}`;
                const kb = `${b[0]},${b[1]}`;
                seen(ka);
                seen(kb);
                union(ka, kb);
            }
            continue;
        }
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
    // Interactive placement (tool === type) feeds the recent-parts list
    // for the canvas context menu; paste/import pass through here too
    // but with an unrelated tool, which keeps recents honest.
    if (state.tool === type)
        noteRecentPart(type);
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
    if (state.tool === 'WIRE') {
        e.preventDefault();
        finalizeWireDraft();
        return;
    }
    // Select tool: double-click a part to edit its value in place.
    if (state.tool === 'select' && !state.moveDraft) {
        const hit = pickAt(eventToWorld(e));
        if (hit && hit.kind === 'part') {
            state.selectedIds.clear();
            state.selectedSegments.clear();
            state.selectedIds.add(hit.id);
            refreshProps();
            render();
            openInlineValueEditor(hit.id);
            e.preventDefault();
        }
    }
});
// ------------------------------------------------------------------
// Floating on-canvas value editor (double-click / F2). Commits on
// Enter or blur; Esc cancels. The document-level keydown handler
// already ignores events targeting inputs, so tool shortcuts stay
// quiet while it's open.
// ------------------------------------------------------------------
let inlineEditEl = null;
function closeInlineValueEditor() {
    if (!inlineEditEl)
        return;
    const elx = inlineEditEl;
    inlineEditEl = null; // null first so blur-commit can't re-enter
    elx.remove();
}
function openInlineValueEditor(partId) {
    const part = state.parts.find(pp => pp.id === partId);
    if (!part || part.type === 'gnd')
        return;
    closeInlineValueEditor();
    const [x0, , x1, y1] = partBBox(part);
    const inp = document.createElement('input');
    inp.className = 'inline-edit';
    inp.value = part.value || '';
    inp.style.left = `${state.pan.x + ((x0 + x1) / 2) * state.zoom}px`;
    inp.style.top = `${state.pan.y + (y1 + 6) * state.zoom}px`;
    inp.style.transform = 'translateX(-50%)';
    wrap.appendChild(inp);
    inlineEditEl = inp;
    const commit = () => {
        if (inlineEditEl !== inp)
            return;
        const v = inp.value.trim();
        closeInlineValueEditor();
        if (v && v !== part.value) {
            part.value = v;
            pushHistory();
            refreshProps();
            render();
            flashHint(`${part.id} value → ${v}`);
        }
    };
    inp.addEventListener('keydown', (ev) => {
        if (ev.key === 'Enter')
            commit();
        else if (ev.key === 'Escape')
            closeInlineValueEditor();
        ev.stopPropagation();
    });
    inp.addEventListener('blur', commit);
    inp.focus();
    inp.select();
}
// ------------------------------------------------------------------
// Duplicate (Ctrl+D): clone the selection one grid step down-right
// and select the clones. Current-controlled sources keep their
// controlling reference, remapped when the controller is part of the
// duplicated set.
// ------------------------------------------------------------------
function duplicateSelection() {
    if (!state.selectedIds.size)
        return;
    const OFF = GRID * 2;
    const idMap = new Map();
    const newPartIds = [];
    const newWireIds = [];
    for (const part of state.parts.filter(pp => state.selectedIds.has(pp.id))) {
        const meta = ELEM_TYPES[part.type];
        const id = nextName(meta ? meta.prefix : part.type.toUpperCase());
        const clone = { ...part, id, x: part.x + OFF, y: part.y + OFF };
        state.parts.push(clone);
        idMap.set(part.id, id);
        newPartIds.push(id);
    }
    for (const w of state.wires.filter(ww => state.selectedIds.has(ww.id))) {
        const id = `W${state.nextId++}`;
        const clone = {
            id,
            points: w.points.map(([x, y]) => [x + OFF, y + OFF]),
        };
        if (w.label !== undefined)
            clone.label = w.label;
        state.wires.push(clone);
        newWireIds.push(id);
    }
    // Remap intra-selection control references (F/H sources).
    for (const id of newPartIds) {
        const clone = state.parts.find(pp => pp.id === id);
        if (clone?.ctrlSrc && idMap.has(clone.ctrlSrc)) {
            clone.ctrlSrc = idMap.get(clone.ctrlSrc);
        }
    }
    state.selectedIds = new Set([...newPartIds, ...newWireIds]);
    state.selectedSegments.clear();
    for (const id of newWireIds)
        selectWholeWire(id);
    markFlash(newPartIds);
    pushHistory();
    refreshProps();
    render();
    const n = newPartIds.length + newWireIds.length;
    flashHint(`Duplicated ${n} item${n === 1 ? '' : 's'} — drag or nudge into place`);
}
function selectAllParts() {
    state.selectedIds = new Set(state.parts.map(pp => pp.id));
    setTool('select');
    refreshProps();
    render();
}
function expandSelectionToNet(wireId) {
    const seed = state.wires.find(w => w.id === wireId);
    if (!seed)
        return;
    const { wireIds } = netMembers(seed);
    state.selectedIds.clear();
    state.selectedSegments.clear();
    for (const id of wireIds)
        selectWholeWire(id);
    flashHint(`Extended to net (${wireIds.size} wire${wireIds.size === 1 ? '' : 's'})`);
    refreshProps();
    render();
}
function flipSelection() {
    let did = false;
    for (const id of state.selectedIds) {
        const pp = state.parts.find(x => x.id === id);
        if (pp) {
            pp.flip = !pp.flip || undefined;
            did = true;
        }
    }
    if (did) {
        pushHistory();
        render();
    }
}
function rotateSelection() {
    let did = false;
    for (const id of state.selectedIds) {
        const pp = state.parts.find(x => x.id === id);
        if (pp) {
            pp.rot = (pp.rot + 90) % 360;
            did = true;
        }
    }
    if (did) {
        pushHistory();
        render();
    }
}
// ---- recents (persisted) ----
const RECENT_CMD_KEY = 'sycan.sedra.cmdk.recent.v1';
const RECENT_PART_KEY = 'sycan.sedra.recentParts.v1';
function readRecentList(key) {
    try {
        const raw = localStorage.getItem(key);
        const arr = raw ? JSON.parse(raw) : null;
        return Array.isArray(arr) ? arr.filter(x => typeof x === 'string') : [];
    }
    catch (_) {
        return [];
    }
}
function writeRecentList(key, list) {
    try {
        localStorage.setItem(key, JSON.stringify(list));
    }
    catch (_) { /* full */ }
}
function noteRecentCommand(id) {
    const list = readRecentList(RECENT_CMD_KEY).filter(x => x !== id);
    list.unshift(id);
    writeRecentList(RECENT_CMD_KEY, list.slice(0, 7));
}
function noteRecentPart(kind) {
    const list = readRecentList(RECENT_PART_KEY).filter(x => x !== kind);
    list.unshift(kind);
    writeRecentList(RECENT_PART_KEY, list.slice(0, 3));
}
function recentParts() {
    const list = readRecentList(RECENT_PART_KEY);
    const seed = list.length ? list : ['res', 'vsrc', 'gnd'];
    return seed.filter(k => k in ELEM_TYPES);
}
function selectSimilar(partId) {
    const seed = state.parts.find(pp => pp.id === partId);
    if (!seed)
        return;
    state.selectedIds = new Set(state.parts.filter(pp => pp.type === seed.type).map(pp => pp.id));
    state.selectedSegments.clear();
    refreshProps();
    render();
    flashHint(`Selected ${state.selectedIds.size} × ${ELEM_TYPES[seed.type].label ?? seed.type}`);
}
// Tab / Shift+Tab cycles the selection through parts in stable id
// order (KiCad-style probe-next-part).
function cycleSelection(dir) {
    if (!state.parts.length)
        return;
    const ordered = [...state.parts].sort((a, b) => a.id.localeCompare(b.id));
    let idx = -1;
    if (state.selectedIds.size === 1) {
        const [cur] = state.selectedIds;
        idx = ordered.findIndex(pp => pp.id === cur);
    }
    const next = ordered[((idx + dir) % ordered.length + ordered.length) % ordered.length];
    state.selectedIds = new Set([next.id]);
    state.selectedSegments.clear();
    refreshProps();
    render();
}
// Renumber every part by type prefix in reading order (top-left →
// bottom-right), remapping control references. One undo step.
function renumberParts() {
    if (!state.parts.length)
        return;
    const ordered = [...state.parts].sort((a, b) => a.y - b.y || a.x - b.x || a.id.localeCompare(b.id));
    const counters = {};
    const idMap = new Map();
    for (const pp of ordered) {
        const prefix = ELEM_TYPES[pp.type]?.prefix ?? 'U';
        counters[prefix] = (counters[prefix] || 0) + 1;
        idMap.set(pp.id, `${prefix}${counters[prefix]}`);
    }
    let changed = 0;
    for (const pp of state.parts) {
        const next = idMap.get(pp.id);
        if (pp.id !== next)
            changed++;
        // Default values track the id (e.g. R1's value "R1") — keep them
        // in sync when they were never customised.
        if (pp.value === pp.id)
            pp.value = next;
        pp.id = next;
    }
    for (const pp of state.parts) {
        if (pp.ctrlSrc && idMap.has(pp.ctrlSrc))
            pp.ctrlSrc = idMap.get(pp.ctrlSrc);
    }
    state.selectedIds = new Set([...state.selectedIds].map(id => idMap.get(id) ?? id));
    state.nameCounters = counters;
    pushHistory();
    refreshProps();
    render();
    notify(`Renumbered ${state.parts.length} parts (${changed} changed)`, 'info');
}
// ---- registry ----
const hasSel = () => state.selectedIds.size > 0 || state.selectedSegments.size > 0;
const clipboardFull = () => ((clipboard.parts?.length ?? 0) + (clipboard.wires?.length ?? 0)) > 0;
const opCount = () => state.selectedIds.size;
const countSuffix = () => (opCount() > 1 ? ` ${opCount()} items` : '');
const COMMANDS = [
    // Hit-target commands (context-menu part / wire / net sections).
    { id: 'part.editValue', title: 'Edit value…', group: 'Edit', shortcut: 'F2',
        menu: { section: 'part', order: 10 },
        enabled: ctx => ctx.hit?.kind === 'part'
            && state.parts.find(pp => pp.id === ctx.hit.id)?.type !== 'gnd',
        run: ctx => openInlineValueEditor(ctx.hit.id) },
    { id: 'edit.rotate', title: 'Rotate 90°', group: 'Edit', shortcut: 'Space', cheat: true,
        menu: { section: 'part', order: 20 },
        enabled: () => [...state.selectedIds].some(id => state.parts.some(pp => pp.id === id)),
        run: () => rotateSelection() },
    { id: 'edit.flip', title: 'Flip horizontally', group: 'Edit', shortcut: 'Y',
        cheat: true,
        menu: { section: 'part', order: 25 },
        enabled: () => [...state.selectedIds].some(id => state.parts.some(pp => pp.id === id)),
        run: () => flipSelection() },
    { id: 'edit.duplicate', title: () => `Duplicate${countSuffix()}`, group: 'Edit',
        shortcut: 'Ctrl+D', cheat: true,
        menu: { section: 'net', order: 30 },
        enabled: () => hasSel(),
        run: () => duplicateSelection() },
    { id: 'edit.copyHere', title: 'Copy (anchor here)', group: 'Edit', shortcut: 'Ctrl+C',
        menu: { section: 'net', order: 40 }, palette: false,
        enabled: () => hasSel(),
        run: ctx => {
            if (ctx.source === 'menu') {
                copySelection(false);
                finalizeCopyAnchor(ctx.snapped);
            }
            else {
                copySelection(false);
            }
        } },
    { id: 'select.similar', title: ctx => {
            const pp = ctx.hit ? state.parts.find(x => x.id === ctx.hit.id) : null;
            return `Select all ${pp ? (ELEM_TYPES[pp.type].label ?? pp.type) : 'similar'}`;
        }, group: 'Selection',
        menu: { section: 'part', order: 30 },
        enabled: ctx => ctx.hit?.kind === 'part',
        run: ctx => selectSimilar(ctx.hit.id) },
    { id: 'edit.renumber', title: 'Renumber parts', group: 'Edit',
        enabled: () => state.parts.length > 0,
        run: () => renumberParts() },
    { id: 'wire.expandNet', title: 'Select whole net', group: 'Selection', shortcut: 'U',
        cheat: true,
        menu: { section: 'wire', order: 10 },
        enabled: ctx => ctx.hit?.kind === 'wire',
        run: ctx => expandSelectionToNet(ctx.hit.id) },
    { id: 'net.highlight', title: 'Highlight net', group: 'Tools',
        menu: { section: 'net', order: 10 },
        enabled: ctx => ctx.hit !== null,
        run: ctx => finalizeNetHighlight(ctx.snapped, ctx.world) },
    { id: 'net.calcHere', title: 'Calc node here', group: 'Tools',
        menu: { section: 'net', order: 20 },
        enabled: ctx => ctx.hit !== null,
        run: ctx => { void finalizeCalcNodePick(ctx.snapped, ctx.world); } },
    { id: 'edit.delete', title: () => `Delete${countSuffix()}`, group: 'Edit',
        shortcut: 'Del', cheat: true, danger: true,
        menu: { section: 'net', order: 90 },
        enabled: () => hasSel(),
        run: () => deleteSelection() },
    // Empty-canvas commands.
    { id: 'edit.pasteHere', title: 'Paste here', group: 'Edit', shortcut: 'Ctrl+V',
        menu: { section: 'canvas', order: 10 }, palette: false,
        enabled: () => clipboardFull(),
        run: ctx => pasteClipboard(ctx.world) },
    { id: 'select.all', title: 'Select all parts', group: 'Selection', shortcut: 'Ctrl+A',
        cheat: true,
        menu: { section: 'canvas', order: 20 },
        enabled: () => state.parts.length > 0,
        run: () => selectAllParts() },
    { id: 'view.fit', title: 'Fit view', group: 'View', shortcut: 'F', cheat: true,
        menu: { section: 'canvas', order: 30 },
        run: () => fitView() },
    // Palette-only commands.
    { id: 'edit.undo', title: 'Undo', group: 'Edit', shortcut: 'Ctrl+Z', cheat: true,
        enabled: () => historyIdx > 0,
        run: () => document.getElementById('btn-undo').click() },
    { id: 'edit.redo', title: 'Redo', group: 'Edit', shortcut: 'Ctrl+Y', cheat: true,
        enabled: () => historyIdx < editHistory.length - 1,
        run: () => document.getElementById('btn-redo').click() },
    { id: 'edit.copy', title: 'Copy (pick anchor)', group: 'Edit', shortcut: 'Ctrl+C',
        cheat: true,
        enabled: () => hasSel(),
        run: () => copySelection(false) },
    { id: 'edit.cut', title: 'Cut (pick anchor)', group: 'Edit', shortcut: 'Ctrl+X',
        enabled: () => hasSel(),
        run: () => copySelection(true) },
    { id: 'edit.paste', title: 'Paste at cursor', group: 'Edit', shortcut: 'Ctrl+V',
        cheat: true,
        enabled: () => clipboardFull(),
        run: () => pasteClipboard() },
    { id: 'tool.select', title: 'Select tool', group: 'Tools', shortcut: 'S / Esc',
        cheat: true, run: () => setTool('select') },
    { id: 'tool.wire', title: 'Wire tool', group: 'Tools', shortcut: 'W',
        cheat: true, run: () => setTool('WIRE') },
    { id: 'tool.delete', title: 'Delete tool', group: 'Tools', shortcut: 'X',
        cheat: true, run: () => setTool('delete') },
    { id: 'tool.rotate', title: 'Rotate tool', group: 'Tools', shortcut: 'B',
        cheat: true, run: () => setTool('rotate') },
    { id: 'tool.highlight', title: 'Net highlight tool', group: 'Tools', shortcut: 'H',
        cheat: true, run: () => setTool('highlight') },
    { id: 'place.byLetter', title: 'Place part', group: 'Tools',
        shortcut: 'R L C V I D G', cheat: true, palette: false,
        run: () => { } },
    { id: 'view.zoomSelection', title: 'Zoom to selection', group: 'View',
        shortcut: 'Shift+F', cheat: true,
        enabled: () => hasSel(),
        run: () => zoomToSelection() },
    { id: 'view.zoom100', title: 'Zoom to 100%', group: 'View', shortcut: 'Ctrl+0',
        cheat: true, run: () => setZoom(1) },
    { id: 'view.zoomIn', title: 'Zoom in', group: 'View', shortcut: 'Ctrl+=',
        run: () => setZoom(state.zoom * ZOOM_STEP) },
    { id: 'view.zoomOut', title: 'Zoom out', group: 'View', shortcut: 'Ctrl+-',
        run: () => setZoom(state.zoom / ZOOM_STEP) },
    { id: 'view.toggleGrid', title: 'Toggle grid', group: 'View',
        run: () => toggleGrid() },
    { id: 'view.toggleErc', title: 'Toggle ERC markers', group: 'View',
        run: () => toggleErc() },
    { id: 'view.matrix', title: 'MNA matrix viewer', group: 'View',
        run: () => document.getElementById('btn-matrix').click() },
    { id: 'view.cheatsheet', title: 'Keyboard shortcuts', group: 'View', shortcut: '?',
        run: () => toggleShortcutOverlay() },
    { id: 'view.palette', title: 'Command palette', group: 'View', shortcut: 'Ctrl+K',
        cheat: true, palette: false,
        run: () => toggleCmdPalette() },
    { id: 'file.exportSvg', title: 'Export schematic SVG', group: 'File',
        menu: { section: 'canvas', order: 50 },
        enabled: () => state.parts.length > 0 || state.wires.length > 0,
        run: () => downloadSchematicSvg() },
    { id: 'file.copyPng', title: 'Copy schematic as PNG', group: 'File',
        enabled: () => state.parts.length > 0 || state.wires.length > 0,
        run: () => { void copySchematicPng(); } },
    { id: 'file.copyNetlist', title: 'Copy netlist', group: 'File',
        run: () => document.getElementById('btn-copy').click() },
    { id: 'file.exportJson', title: 'Export JSON', group: 'File',
        run: () => document.getElementById('btn-export-json').click() },
    { id: 'file.importJson', title: 'Import JSON…', group: 'File',
        run: () => document.getElementById('btn-import-json').click() },
    { id: 'file.clearAll', title: 'Clear schematic…', group: 'File', danger: true,
        enabled: () => state.parts.length > 0 || state.wires.length > 0,
        run: () => document.getElementById('btn-clear').click() },
];
// Place commands for every part kind (palette).
for (const kind of Object.keys(ELEM_TYPES)) {
    const letter = { res: 'R', ind: 'L', cap: 'C', vsrc: 'V', isrc: 'I',
        diode: 'D', gnd: 'G' }[kind];
    COMMANDS.push({
        id: `place.${kind}`,
        title: `Place ${ELEM_TYPES[kind].label ?? kind}`,
        group: 'Place',
        shortcut: letter,
        run: () => setTool(kind),
    });
}
const COMMANDS_BY_ID = new Map(COMMANDS.map(c => [c.id, c]));
function makeCtx(world, hit, source) {
    return { world, snapped: snapPt(world), hit, source };
}
function viewportCenterWorld() {
    return [(wrap.clientWidth / 2 - state.pan.x) / state.zoom,
        (wrap.clientHeight / 2 - state.pan.y) / state.zoom];
}
function cmdTitle(c, ctx) {
    return typeof c.title === 'string' ? c.title : c.title(ctx);
}
function runCommand(c, ctx) {
    if (c.enabled && !c.enabled(ctx))
        return;
    noteRecentCommand(c.id);
    c.run(ctx);
}
// Render a shortcut label as <kbd> chips; ' / ' separates alternates.
function kbdHtml(label) {
    return label.split(' / ').map(part => `<kbd>${part}</kbd>`).join(' / ');
}
// ------------------------------------------------------------------
// Right-click context menu
// ------------------------------------------------------------------
let ctxMenuEl = null;
let ctxMenuCleanup = null;
function closeContextMenu() {
    if (ctxMenuCleanup) {
        ctxMenuCleanup();
        ctxMenuCleanup = null;
    }
    if (ctxMenuEl) {
        ctxMenuEl.remove();
        ctxMenuEl = null;
    }
}
const MENU_SECTION_ORDER = ['part', 'wire', 'net', 'canvas'];
function sectionVisible(section, hit) {
    switch (section) {
        case 'part': return hit?.kind === 'part';
        case 'wire': return hit?.kind === 'wire';
        case 'net': return hit !== null;
        case 'canvas': return hit === null;
    }
}
function openContextMenu(sx, sy) {
    closeContextMenu();
    closeCmdPalette();
    if (shortcutOverlayEl)
        toggleShortcutOverlay();
    const world = eventToWorld({ clientX: sx, clientY: sy });
    const hit = pickAt(world);
    // KiCad selection rule: right-click on an unselected item re-targets
    // the selection to it; on a selected item the multi-selection is
    // kept so verbs apply to all of it; empty space keeps selection.
    if (hit && !state.selectedIds.has(hit.id)) {
        if (state.tool !== 'select')
            setTool('select');
        state.selectedIds = new Set([hit.id]);
        state.selectedSegments.clear();
        if (hit.kind === 'wire')
            selectWholeWire(hit.id);
        refreshProps();
        render();
    }
    const ctx = makeCtx(world, hit, 'menu');
    const menu = document.createElement('div');
    menu.className = 'ctx-menu';
    menu.setAttribute('role', 'menu');
    const rows = [];
    let lastSection = null;
    const addItem = (cmd, title) => {
        const item = document.createElement('div');
        item.className = 'ctx-item' + (cmd.danger ? ' danger' : '');
        item.setAttribute('role', 'menuitem');
        const enabled = !cmd.enabled || cmd.enabled(ctx);
        if (!enabled)
            item.setAttribute('aria-disabled', 'true');
        const label = document.createElement('span');
        label.className = 'ctx-label';
        label.textContent = title;
        item.appendChild(label);
        if (cmd.shortcut) {
            const keys = document.createElement('span');
            keys.className = 'ctx-keys';
            keys.innerHTML = kbdHtml(cmd.shortcut);
            item.appendChild(keys);
        }
        menu.appendChild(item);
        if (enabled)
            rows.push({ el: item, cmd });
        return item;
    };
    for (const section of MENU_SECTION_ORDER) {
        if (!sectionVisible(section, hit))
            continue;
        const cmds = COMMANDS
            .filter(c => c.menu && c.menu.section === section)
            .sort((a, b) => a.menu.order - b.menu.order);
        if (!cmds.length)
            continue;
        if (lastSection !== null) {
            const sep = document.createElement('div');
            sep.className = 'ctx-sep';
            sep.setAttribute('role', 'separator');
            menu.appendChild(sep);
        }
        lastSection = section;
        for (const cmd of cmds)
            addItem(cmd, cmdTitle(cmd, ctx));
    }
    // Recent parts on empty canvas.
    if (!hit) {
        const recents = recentParts();
        if (recents.length) {
            const sep = document.createElement('div');
            sep.className = 'ctx-sep';
            menu.appendChild(sep);
            const cap = document.createElement('div');
            cap.className = 'ctx-caption';
            cap.textContent = 'Place recent';
            menu.appendChild(cap);
            for (const kind of recents) {
                const cmd = COMMANDS_BY_ID.get(`place.${kind}`);
                if (cmd)
                    addItem(cmd, cmdTitle(cmd, ctx));
            }
        }
    }
    document.body.appendChild(menu);
    ctxMenuEl = menu;
    // Clamp into the viewport; flip when overflowing.
    const r = menu.getBoundingClientRect();
    let left = sx, top = sy;
    if (left + r.width > window.innerWidth - 8)
        left = Math.max(8, sx - r.width);
    if (top + r.height > window.innerHeight - 8)
        top = Math.max(8, sy - r.height);
    menu.style.left = `${left}px`;
    menu.style.top = `${top}px`;
    // Shared active index for mouse + keyboard.
    let active = -1;
    const setActive = (idx) => {
        if (active >= 0)
            rows[active].el.classList.remove('active');
        active = idx;
        if (active >= 0)
            rows[active].el.classList.add('active');
    };
    rows.forEach((row, idx) => {
        row.el.addEventListener('mouseenter', () => setActive(idx));
        row.el.addEventListener('mouseup', () => {
            closeContextMenu();
            runCommand(row.cmd, ctx);
        });
    });
    const onKey = (ev) => {
        if (ev.key === 'Escape') {
            closeContextMenu();
        }
        else if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
            const dir = ev.key === 'ArrowDown' ? 1 : -1;
            const n = rows.length;
            if (n)
                setActive(((active + dir) % n + n) % n);
        }
        else if (ev.key === 'Home' && rows.length) {
            setActive(0);
        }
        else if (ev.key === 'End' && rows.length) {
            setActive(rows.length - 1);
        }
        else if (ev.key === 'Enter' && active >= 0) {
            const row = rows[active];
            closeContextMenu();
            runCommand(row.cmd, ctx);
        }
        else {
            return;
        }
        ev.preventDefault();
        ev.stopPropagation();
    };
    const onDown = (ev) => {
        if (ctxMenuEl && !ctxMenuEl.contains(ev.target))
            closeContextMenu();
    };
    const onAway = () => closeContextMenu();
    document.addEventListener('keydown', onKey, true);
    document.addEventListener('mousedown', onDown, true);
    window.addEventListener('blur', onAway);
    window.addEventListener('resize', onAway);
    wrap.addEventListener('wheel', onAway, { passive: true });
    ctxMenuCleanup = () => {
        document.removeEventListener('keydown', onKey, true);
        document.removeEventListener('mousedown', onDown, true);
        window.removeEventListener('blur', onAway);
        window.removeEventListener('resize', onAway);
        wrap.removeEventListener('wheel', onAway);
    };
}
// ------------------------------------------------------------------
// Ctrl+K command palette
// ------------------------------------------------------------------
let cmdkEl = null;
function closeCmdPalette() {
    if (cmdkEl) {
        cmdkEl.remove();
        cmdkEl = null;
    }
}
// Case-insensitive subsequence fuzzy match. Returns null when query is
// not a subsequence; otherwise a score (higher = better) + matched
// indices for highlight.
function fuzzyMatch(query, haystack) {
    const q = query.toLowerCase();
    const h = haystack.toLowerCase();
    let score = 0;
    let hi = 0;
    let prev = -2;
    const indices = [];
    for (let qi = 0; qi < q.length; qi++) {
        const ch = q[qi];
        let found = -1;
        for (let k = hi; k < h.length; k++) {
            if (h[k] === ch) {
                found = k;
                break;
            }
        }
        if (found === -1)
            return null;
        score += 1;
        if (found === prev + 1)
            score += 2; // consecutive
        if (found === 0 || h[found - 1] === ' ' || h[found - 1] === '(')
            score += 3;
        score -= (found - hi) * 0.05; // gap penalty
        indices.push(found);
        prev = found;
        hi = found + 1;
    }
    return { score, indices };
}
function toggleCmdPalette() {
    if (cmdkEl) {
        closeCmdPalette();
        return;
    }
    closeContextMenu();
    if (shortcutOverlayEl)
        toggleShortcutOverlay();
    const overlay = document.createElement('div');
    overlay.className = 'cmdk-overlay';
    const card = document.createElement('div');
    card.className = 'cmdk';
    card.setAttribute('role', 'dialog');
    card.setAttribute('aria-label', 'Command palette');
    const input = document.createElement('input');
    input.className = 'cmdk-input';
    input.type = 'text';
    input.placeholder = 'Type a command…';
    input.spellcheck = false;
    input.autocomplete = 'off';
    const list = document.createElement('div');
    list.className = 'cmdk-list';
    list.setAttribute('role', 'listbox');
    const empty = document.createElement('div');
    empty.className = 'cmdk-empty';
    empty.textContent = 'No matching commands';
    empty.hidden = true;
    card.appendChild(input);
    card.appendChild(list);
    card.appendChild(empty);
    overlay.appendChild(card);
    const ctx = makeCtx(viewportCenterWorld(), null, 'palette');
    const eligible = () => COMMANDS.filter(c => c.palette !== false && (!c.enabled || c.enabled(ctx)));
    let entries = [];
    let active = 0;
    const setActive = (idx) => {
        entries[active]?.el.classList.remove('active');
        active = idx;
        const e = entries[active];
        if (e) {
            e.el.classList.add('active');
            e.el.scrollIntoView({ block: 'nearest' });
        }
    };
    const makeRow = (cmd, titleHtml, showGroup) => {
        const item = document.createElement('div');
        item.className = 'cmdk-item' + (cmd.danger ? ' danger' : '');
        item.setAttribute('role', 'option');
        const title = document.createElement('span');
        title.className = 'cmdk-title';
        title.innerHTML = titleHtml;
        item.appendChild(title);
        if (showGroup) {
            // Group tag is useful in flat/mixed lists (search, Recent) but
            // redundant noise under a same-group caption.
            const group = document.createElement('span');
            group.className = 'cmdk-group';
            group.textContent = cmd.group;
            item.appendChild(group);
        }
        if (cmd.shortcut) {
            const keys = document.createElement('span');
            keys.className = 'ctx-keys';
            keys.innerHTML = kbdHtml(cmd.shortcut);
            item.appendChild(keys);
        }
        item.addEventListener('mouseenter', () => {
            const idx = entries.findIndex(en => en.el === item);
            if (idx >= 0)
                setActive(idx);
        });
        item.addEventListener('mouseup', () => {
            closeCmdPalette();
            runCommand(cmd, ctx);
        });
        return item;
    };
    const escapeHtmlLocal = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    const refill = () => {
        const q = input.value.trim();
        list.innerHTML = '';
        entries = [];
        const recents = readRecentList(RECENT_CMD_KEY);
        const pool = eligible();
        if (!q) {
            // Recents first, then grouped catalog.
            const recentCmds = recents
                .map(id => pool.find(c => c.id === id))
                .filter((c) => !!c);
            if (recentCmds.length) {
                const cap = document.createElement('div');
                cap.className = 'cmdk-caption';
                cap.textContent = 'Recent';
                list.appendChild(cap);
                for (const cmd of recentCmds) {
                    const el = makeRow(cmd, escapeHtmlLocal(cmdTitle(cmd, ctx)), true);
                    list.appendChild(el);
                    entries.push({ cmd, el });
                }
            }
            for (const groupName of ['Edit', 'Selection', 'Tools', 'Place', 'View', 'File']) {
                const cmds = pool.filter(c => c.group === groupName);
                if (!cmds.length)
                    continue;
                const cap = document.createElement('div');
                cap.className = 'cmdk-caption';
                cap.textContent = groupName;
                list.appendChild(cap);
                for (const cmd of cmds) {
                    const el = makeRow(cmd, escapeHtmlLocal(cmdTitle(cmd, ctx)), false);
                    list.appendChild(el);
                    entries.push({ cmd, el });
                }
            }
        }
        else {
            const scored = pool
                .map(cmd => {
                const title = cmdTitle(cmd, ctx);
                const m = fuzzyMatch(q, `${title} ${cmd.group}`);
                if (!m)
                    return null;
                const recIdx = recents.indexOf(cmd.id);
                return { cmd, title, m, rec: recIdx === -1 ? 99 : recIdx };
            })
                .filter((x) => !!x)
                .sort((a, b) => b.m.score - a.m.score || a.rec - b.rec);
            for (const { cmd, title, m } of scored) {
                const marks = new Set(m.indices.filter(ix => ix < title.length));
                let html = '';
                for (let ix = 0; ix < title.length; ix++) {
                    const c = escapeHtmlLocal(title[ix]);
                    html += marks.has(ix) ? `<b>${c}</b>` : c;
                }
                const el = makeRow(cmd, html, true);
                list.appendChild(el);
                entries.push({ cmd, el });
            }
        }
        empty.hidden = entries.length > 0;
        active = 0;
        if (entries.length)
            entries[0].el.classList.add('active');
    };
    input.addEventListener('input', refill);
    card.addEventListener('keydown', (ev) => {
        if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
            const dir = ev.key === 'ArrowDown' ? 1 : -1;
            const n = entries.length;
            if (n)
                setActive(((active + dir) % n + n) % n);
        }
        else if (ev.key === 'Home' && entries.length) {
            setActive(0);
        }
        else if (ev.key === 'End' && entries.length) {
            setActive(entries.length - 1);
        }
        else if (ev.key === 'Enter') {
            const e = entries[active];
            closeCmdPalette();
            if (e)
                runCommand(e.cmd, ctx);
        }
        else if (ev.key === 'Escape' || (ev.key.toLowerCase() === 'k' && (ev.ctrlKey || ev.metaKey))) {
            closeCmdPalette();
        }
        else {
            ev.stopPropagation();
            return;
        }
        ev.preventDefault();
        ev.stopPropagation();
    });
    overlay.addEventListener('mousedown', (ev) => {
        if (ev.target === overlay)
            closeCmdPalette();
    });
    document.body.appendChild(overlay);
    cmdkEl = overlay;
    refill();
    input.focus();
}
// ------------------------------------------------------------------
// '?' cheat sheet — derived from the registry plus gesture rows that
// aren't commands.
// ------------------------------------------------------------------
const STATIC_SHORTCUT_ROWS = {
    Selection: [
        ['Click', 'Select part / segment'],
        ['Shift+click', 'Add to selection'],
        ['Ctrl+click', 'Toggle'],
        ['Drag', 'Move (wires follow)'],
        ['Right-click', 'Context menu'],
        ['Tab / Shift+Tab', 'Cycle through parts'],
        ['M', 'Move with cursor'],
    ],
    View: [
        ['Scroll', 'Pan'],
        ['Ctrl+scroll', 'Zoom'],
        ['Mid/right-drag', 'Pan'],
        ['?', 'This cheat sheet'],
    ],
    Edit: [
        ['F2 / dbl-click', 'Edit part value'],
        ['← ↑ ↓ →', 'Nudge selection'],
    ],
};
const CHEAT_GROUP_ORDER = ['Tools', 'Edit', 'Selection', 'View'];
function shortcutGroups() {
    const ctx = makeCtx([0, 0], null, 'palette');
    return CHEAT_GROUP_ORDER.map(title => ({
        title,
        rows: [
            ...COMMANDS.filter(c => c.cheat && c.shortcut && c.group === title)
                .map(c => [c.shortcut, cmdTitle(c, ctx)]),
            ...(STATIC_SHORTCUT_ROWS[title] ?? []),
        ],
    })).filter(g => g.rows.length > 0);
}
let shortcutOverlayEl = null;
function toggleShortcutOverlay() {
    if (shortcutOverlayEl) {
        shortcutOverlayEl.remove();
        shortcutOverlayEl = null;
        return;
    }
    const overlay = document.createElement('div');
    overlay.className = 'shortcut-overlay';
    const card = document.createElement('div');
    card.className = 'shortcut-card';
    const h2 = document.createElement('h2');
    h2.textContent = 'Keyboard shortcuts';
    card.appendChild(h2);
    const cols = document.createElement('div');
    cols.className = 'shortcut-cols';
    for (const group of shortcutGroups()) {
        const g = document.createElement('div');
        g.className = 'shortcut-group';
        const h4 = document.createElement('h4');
        h4.textContent = group.title;
        g.appendChild(h4);
        for (const [keys, desc] of group.rows) {
            const row = document.createElement('div');
            row.className = 'shortcut-row';
            const d = document.createElement('span');
            d.textContent = desc;
            const k = document.createElement('span');
            k.className = 'keys';
            k.innerHTML = kbdHtml(keys);
            row.appendChild(d);
            row.appendChild(k);
            g.appendChild(row);
        }
        cols.appendChild(g);
    }
    card.appendChild(cols);
    overlay.appendChild(card);
    overlay.addEventListener('mousedown', (ev) => {
        if (ev.target === overlay)
            toggleShortcutOverlay();
    });
    document.body.appendChild(overlay);
    shortcutOverlayEl = overlay;
}
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
        info.style.cssText = 'font-size: var(--text-md); color: var(--fg);';
        propPane.appendChild(info);
        const list = document.createElement('div');
        list.style.cssText = 'margin-top: 8px; color: var(--muted); ' +
            'font-size: var(--text-xs); font-family: var(--font-mono);';
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
        // Selection in this editor is segment-level (see comment on
        // ``state.selectedSegments``), so the Id row prints the
        // composite ``${wireId}|${segIdx}`` keys for each selected
        // segment of this wire instead of just the wire id. A wire
        // selected end-to-end shows every segment in order — that's
        // the canonical representation; there's no whole-wire id.
        const segIdxs = wireSelectedSegments(wire.id);
        const idText = segIdxs.length
            ? segIdxs.map(i => `${wire.id}|${i}`).join(', ')
            : wire.id;
        const lab2 = document.createElement('label');
        const lab2Sp = document.createElement('span');
        lab2Sp.textContent = 'Id';
        const lab2Val = document.createElement('span');
        lab2Val.textContent = idText;
        lab2Val.style.cssText =
            'font-family: var(--font-mono); ' +
                'word-break: break-all; text-align: right;';
        lab2.appendChild(lab2Sp);
        lab2.appendChild(lab2Val);
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
        info.style.cssText = 'color: var(--muted); font-size: var(--text-xs); margin-top: 6px;';
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
        // For devices that take a model name + a positional parameter
        // tail (D / Q / M / triode), the field is labelled "Model" so the
        // accompanying "Params" field reads correctly. Plain passives
        // and sources keep the historical "Value" label.
        const valueLabel = needsParamsField(p.type) ? 'Model' : 'Value';
        mk(valueLabel, p.value || '', (v) => {
            p.value = v;
            pushHistory();
            render();
        });
    }
    if (needsParamsField(p.type)) {
        // Show the user the *resolved* tail (defaultParams when blank) so
        // they can see what's actually emitted. If the user clears the
        // field we wipe params back to undefined and fall through to the
        // per-instance symbolic default.
        const placeholder = defaultParams(p);
        const cur = (p.params ?? '').trim();
        mk('Params', cur, (v) => {
            const next = v.trim();
            p.params = next ? next : undefined;
            pushHistory();
            render();
        });
        if (!cur) {
            const hint = document.createElement('div');
            hint.style.cssText = 'color: var(--muted); font-size: var(--text-xs); margin: -4px 0 6px 68px;';
            hint.textContent = `default: ${placeholder}`;
            propPane.appendChild(hint);
        }
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
    info.style.cssText = 'color: var(--muted); font-size: var(--text-xs); margin-top: 6px;';
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
        if (w.bad) {
            // Bad wires keep their endpoints' net connection — the user
            // will re-route the visual placeholder into a real Manhattan
            // path later, but the netlist must stay correct in the
            // meantime. Union start↔end without contributing to interior
            // / mid-segment crossings (the diagonal body has no meaningful
            // interior).
            if (w.points.length >= 2) {
                const a = w.points[0];
                const b = w.points[w.points.length - 1];
                const ka = `${a[0]},${a[1]}`;
                const kb = `${b[0]},${b[1]}`;
                seen(ka);
                seen(kb);
                union(ka, kb);
            }
            continue;
        }
        for (const pt of w.points)
            seen(`${pt[0]},${pt[1]}`);
        for (let i = 1; i < w.points.length; i++) {
            union(`${w.points[i - 1][0]},${w.points[i - 1][1]}`, `${w.points[i][0]},${w.points[i][1]}`);
        }
    }
    // Mid-segment crossings: a wire whose segment passes strictly
    // through another wire's vertex / part terminal still connects
    // the two even when no explicit T-vertex sits on the segment.
    // (`simplifyCollinearVertices` aggressively drops collinear-only
    // interior vertices, so the explicit T may have been removed.)
    // For each segment, walk every other interest-point and union
    // the crossing into the segment's endpoints when it lies inside.
    const interestPoints = [];
    for (const { terminals } of partTerms) {
        for (const t of terminals)
            interestPoints.push(t.pos);
    }
    for (const w of state.wires) {
        if (w.bad) {
            // Bad wires only contribute their endpoints as interest points
            // (so a normal wire that happens to pass through a bad wire's
            // endpoint cell still unions). Their interior is meaningless.
            if (w.points.length >= 2) {
                interestPoints.push(w.points[0]);
                interestPoints.push(w.points[w.points.length - 1]);
            }
            continue;
        }
        for (const pt of w.points)
            interestPoints.push(pt);
    }
    for (const w of state.wires) {
        if (w.bad)
            continue; // bad wires' diagonal body never hosts crossings
        for (let i = 1; i < w.points.length; i++) {
            const a = w.points[i - 1], b = w.points[i];
            // pointOnSegment requires axis-aligned input — that's the
            // invariant we keep for every wire segment, so it's safe.
            for (const p of interestPoints) {
                if (pointOnSegment(p, a, b)) {
                    union(`${p[0]},${p[1]}`, `${a[0]},${a[1]}`);
                }
            }
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
        if (w.bad)
            continue;
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
    lines.push(`* generated ${new Date().toISOString().slice(0, 10)}`);
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
    hint.classList.add('flash');
    if (hintTimer !== null)
        clearTimeout(hintTimer);
    hintTimer = window.setTimeout(() => {
        // Null the timer BEFORE refreshHint so its flash-guard lets the
        // persistent hint through.
        hintTimer = null;
        hint.classList.remove('flash');
        refreshHint();
    }, 1600);
}
const ERC_VISIBLE_KEY = 'sycan.sedra.erc.v1';
let ercVisible = true;
try {
    ercVisible = localStorage.getItem(ERC_VISIBLE_KEY) !== '0';
}
catch (_) { /* ok */ }
let ercCache = [];
let ercCycle = 0;
function toggleErc() {
    ercVisible = !ercVisible;
    try {
        localStorage.setItem(ERC_VISIBLE_KEY, ercVisible ? '1' : '0');
    }
    catch (_) { /* ok */ }
    refreshErc();
    render();
    flashHint(ercVisible ? 'ERC markers shown' : 'ERC markers hidden');
}
// Is this grid point a valid wire-connection target — a part
// terminal, a wire endpoint/vertex, or on a wire segment?
function isConnectionTarget(pt) {
    const [x, y] = pt;
    for (const pp of state.parts) {
        for (const term of partTerminals(pp)) {
            if (term.pos[0] === x && term.pos[1] === y)
                return true;
        }
    }
    for (const w of state.wires) {
        if (w.bad || w.points.length < 2)
            continue;
        for (const p2 of w.points) {
            if (p2[0] === x && p2[1] === y)
                return true;
        }
        for (let i = 0; i < w.points.length - 1; i++) {
            const a = w.points[i], b = w.points[i + 1];
            if (a[0] === b[0] && x === a[0]
                && y > Math.min(a[1], b[1]) && y < Math.max(a[1], b[1]))
                return true;
            if (a[1] === b[1] && y === a[1]
                && x > Math.min(a[0], b[0]) && x < Math.max(a[0], b[0]))
                return true;
        }
    }
    return false;
}
function ercFindings() {
    const out = [];
    if (!state.parts.length && !state.wires.length)
        return out;
    // Occupancy maps: wire endpoint/body points and terminal points.
    const wirePoint = new Map(); // grid key -> wire ids
    for (const w of state.wires) {
        if (w.bad || w.points.length < 2)
            continue;
        for (const pt of w.points) {
            const k = `${pt[0]},${pt[1]}`;
            const list = wirePoint.get(k) ?? [];
            list.push(w.id);
            wirePoint.set(k, list);
        }
        // Mid-segment coverage at grid pitch (terminals can tap segments).
        for (let i = 0; i < w.points.length - 1; i++) {
            const a = w.points[i], b = w.points[i + 1];
            if (a[0] === b[0]) {
                const lo = Math.min(a[1], b[1]), hi = Math.max(a[1], b[1]);
                for (let y = lo; y <= hi; y += GRID) {
                    const k = `${a[0]},${y}`;
                    const list = wirePoint.get(k) ?? [];
                    if (!list.includes(w.id))
                        list.push(w.id);
                    wirePoint.set(k, list);
                }
            }
            else if (a[1] === b[1]) {
                const lo = Math.min(a[0], b[0]), hi = Math.max(a[0], b[0]);
                for (let x = lo; x <= hi; x += GRID) {
                    const k = `${x},${a[1]}`;
                    const list = wirePoint.get(k) ?? [];
                    if (!list.includes(w.id))
                        list.push(w.id);
                    wirePoint.set(k, list);
                }
            }
        }
    }
    const termPoint = new Map();
    for (const pp of state.parts) {
        for (const term of partTerminals(pp)) {
            const k = `${term.pos[0]},${term.pos[1]}`;
            const list = termPoint.get(k) ?? [];
            list.push(pp.id);
            termPoint.set(k, list);
        }
    }
    // 1. Floating part terminals: no wire and no coincident terminal.
    for (const pp of state.parts) {
        if (pp.type === 'gnd')
            continue;
        for (const term of partTerminals(pp)) {
            const k = `${term.pos[0]},${term.pos[1]}`;
            const wires = wirePoint.get(k) ?? [];
            const terms = termPoint.get(k) ?? [];
            if (!wires.length && terms.length <= 1) {
                out.push({ level: 'warn',
                    msg: `${pp.id}: unconnected terminal (${term.name})`,
                    at: term.pos, ids: [pp.id] });
            }
        }
    }
    // 2. Dangling wire endpoints (free ends that touch nothing).
    for (const w of state.wires) {
        if (w.bad || w.points.length < 2)
            continue;
        for (const idx of [0, w.points.length - 1]) {
            const pt = w.points[idx];
            const k = `${pt[0]},${pt[1]}`;
            const wires = wirePoint.get(k) ?? [];
            const terms = termPoint.get(k) ?? [];
            if (wires.length <= 1 && !terms.length) {
                out.push({ level: 'warn',
                    msg: `${w.id}: dangling wire end`,
                    at: pt, ids: [w.id] });
            }
        }
    }
    // 3. Duplicate reference designators (possible after JSON import).
    const seenIds = new Map();
    for (const pp of state.parts) {
        const prev = seenIds.get(pp.id);
        if (prev) {
            out.push({ level: 'error',
                msg: `Duplicate designator ${pp.id}`,
                at: [pp.x, pp.y], ids: [pp.id] });
        }
        else {
            seenIds.set(pp.id, pp);
        }
    }
    // 4. Missing ground reference.
    if (state.parts.length && !state.parts.some(pp => pp.type === 'gnd')) {
        const first = state.parts[0];
        const [bx0, by0] = partBBox(first);
        out.push({ level: 'error',
            msg: 'No ground reference (add a GND part)',
            at: [bx0, by0], ids: [first.id] });
    }
    return out;
}
function refreshErc() {
    ercCache = ercVisible ? ercFindings() : [];
    const zone = document.getElementById('sb-erc');
    if (!zone)
        return;
    if (!ercCache.length) {
        zone.classList.add('hidden');
        return;
    }
    const errors = ercCache.filter(f => f.level === 'error').length;
    const warns = ercCache.length - errors;
    const frags = [];
    if (errors)
        frags.push(`${errors} error${errors === 1 ? '' : 's'}`);
    if (warns)
        frags.push(`${warns} warning${warns === 1 ? '' : 's'}`);
    zone.textContent = `⚠ ${frags.join(', ')}`;
    zone.classList.toggle('erc-errors', errors > 0);
    zone.classList.remove('hidden');
}
function focusErcFinding(f) {
    state.selectedIds = new Set(f.ids);
    state.selectedSegments.clear();
    for (const id of f.ids) {
        if (state.wires.some(w => w.id === id))
            selectWholeWire(id);
    }
    refreshProps();
    zoomToSelection();
    flashHint(f.msg);
}
function drawErcMarkers() {
    if (!ercVisible || !ercCache.length)
        return;
    ercCache.forEach((f, idx) => {
        const g = el('g', {
            class: `erc-marker${f.level === 'error' ? ' erc-error' : ''}`,
            'data-erc': String(idx),
        }, svg);
        // Badge floats up-right of the problem point (screen-constant
        // offset) so the underlying terminal / part stays clickable; a
        // hairline leader ties it back to the exact spot.
        const off = 14 / state.zoom;
        const bx = f.at[0] + off, by = f.at[1] - off;
        el('path', {
            d: `M${f.at[0]},${f.at[1]} L${bx},${by}`,
            stroke: 'currentColor', 'stroke-width': 1, opacity: 0.5,
            'vector-effect': 'non-scaling-stroke', fill: 'none',
            'pointer-events': 'none',
        }, g);
        // Counter-scaled inner group: circle AND text are authored in
        // fixed screen pixels (CSS font-size stays 9px on screen at any
        // zoom).
        const badge = el('g', {
            transform: `translate(${bx} ${by}) scale(${1 / state.zoom})`,
        }, g);
        el('circle', { cx: 0, cy: 0, r: 7 }, badge);
        el('text', { x: 0, y: 0.5 }, badge).textContent = '!';
        g.addEventListener('mousedown', (ev) => {
            ev.stopPropagation();
            focusErcFinding(f);
        });
    });
}
// ------------------------------------------------------------------
// Schematic export: standalone SVG download + 2x PNG to clipboard.
//
// The live canvas SVG is re-rendered with every interaction layer
// suppressed (selection, hover, crosshair, marquee), cloned, stripped
// of editor-only nodes, given a tight viewBox, and equipped with a
// <style> block whose token values are resolved from the current
// theme — the exported file must stand alone without the app's CSS.
// ------------------------------------------------------------------
function schematicBBox() {
    if (!state.parts.length && !state.wires.length)
        return null;
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const pp of state.parts) {
        const [a, b, c, d] = partBBox(pp);
        x0 = Math.min(x0, a);
        y0 = Math.min(y0, b);
        x1 = Math.max(x1, c);
        y1 = Math.max(y1, d);
    }
    for (const w of state.wires) {
        for (const [x, y] of w.points) {
            x0 = Math.min(x0, x);
            y0 = Math.min(y0, y);
            x1 = Math.max(x1, x);
            y1 = Math.max(y1, y);
        }
    }
    // Room for the name/value labels hanging off the right edge.
    return [x0 - 20, y0 - 20, x1 + 90, y1 + 20];
}
function exportSchematicSvg() {
    const bbox = schematicBBox();
    if (!bbox)
        return null;
    // Re-render with every interaction/overlay layer suppressed — the
    // export style block has no rules for them, so any leak renders as
    // solid black default fills.
    const savedSel = state.selectedIds;
    const savedSegs = state.selectedSegments;
    const savedCursor = state.cursorInside;
    const savedHover = hoverTarget;
    const savedBox = state.boxSelect;
    const savedCalcHl = state.calcNodeHighlight;
    const savedNetHl = state.netHighlightOverlay;
    const savedDraft = state.wireDraft;
    const savedPreview = placementPreview;
    const savedMatrixHl = matrixHighlightPartIds;
    const savedErc = ercCache;
    state.selectedIds = new Set();
    state.selectedSegments = new Set();
    state.cursorInside = false;
    hoverTarget = null;
    state.boxSelect = null;
    state.calcNodeHighlight = null;
    state.netHighlightOverlay = null;
    state.wireDraft = null;
    placementPreview = null;
    matrixHighlightPartIds = new Set();
    ercCache = [];
    render();
    const clone = svg.cloneNode(true);
    state.selectedIds = savedSel;
    state.selectedSegments = savedSegs;
    state.cursorInside = savedCursor;
    hoverTarget = savedHover;
    state.boxSelect = savedBox;
    state.calcNodeHighlight = savedCalcHl;
    state.netHighlightOverlay = savedNetHl;
    state.wireDraft = savedDraft;
    placementPreview = savedPreview;
    matrixHighlightPartIds = savedMatrixHl;
    ercCache = savedErc;
    render();
    // Strip editor-only layers (belt-and-braces for future layers).
    clone.querySelector('#hit-layer')?.remove();
    clone.querySelectorAll('[data-layer="grid"]').forEach(n => n.remove());
    clone.querySelectorAll('.erc-marker, .net-highlight, .net-highlight-term, ' +
        '.calc-node-highlight, .matrix-part-highlight, .preview, ' +
        '.snap-ring, .flash-halo').forEach(n => n.remove());
    const [x0, y0, x1, y1] = bbox;
    clone.setAttribute('viewBox', `${x0} ${y0} ${x1 - x0} ${y1 - y0}`);
    clone.setAttribute('width', String(x1 - x0));
    clone.setAttribute('height', String(y1 - y0));
    clone.removeAttribute('style');
    // Inline the theme tokens the SVG classes reference.
    const cs = getComputedStyle(document.documentElement);
    const v = (name, fallback) => (cs.getPropertyValue(name) || fallback).trim() || fallback;
    const stroke = v('--stroke', '#1b1b1f');
    const bg = v('--surface-0', '#ffffff');
    const mono = v('--font-mono', 'monospace');
    const ui = v('--font-ui', 'sans-serif');
    const style = document.createElementNS(SVGNS, 'style');
    style.textContent = `
    svg { background: ${bg}; }
    .wire { fill: none; stroke: ${stroke}; stroke-width: 1.8;
            stroke-linecap: round; stroke-linejoin: round; }
    .wire-bad { stroke: ${v('--error', '#d24343')}; stroke-dasharray: 6 3; }
    .glyph { color: ${stroke}; }
    .part-body { fill: none; stroke: ${stroke}; stroke-width: 1.6;
                 stroke-linecap: round; stroke-linejoin: round; }
    .part-fill { fill: ${bg}; stroke: ${stroke}; stroke-width: 1.6; }
    .part-text { fill: ${stroke}; font-size: 11px; font-family: ${mono}; }
    .part-name { fill: ${stroke}; font-size: 11px; font-weight: 600;
                 font-family: ${ui}; }
    .terminal { fill: ${v('--terminal', '#44444a')}; stroke: none; }
    .node-dot { fill: ${stroke}; stroke: none; }
    .net-label-text { fill: ${stroke}; font-size: 11px; font-weight: 600;
                      font-family: ${mono}; }
    .net-label-bg { fill: ${bg}; stroke: ${stroke}; stroke-width: 1; }
  `;
    clone.insertBefore(style, clone.firstChild);
    // Opaque background rect so PNG rasterisation isn't transparent.
    const bgRect = document.createElementNS(SVGNS, 'rect');
    bgRect.setAttribute('x', String(x0));
    bgRect.setAttribute('y', String(y0));
    bgRect.setAttribute('width', String(x1 - x0));
    bgRect.setAttribute('height', String(y1 - y0));
    bgRect.setAttribute('fill', bg);
    clone.insertBefore(bgRect, style.nextSibling);
    return new XMLSerializer().serializeToString(clone);
}
function downloadSchematicSvg() {
    const markup = exportSchematicSvg();
    if (!markup) {
        flashHint('Nothing to export — the canvas is empty');
        return;
    }
    const blob = new Blob([markup], { type: 'image/svg+xml' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'sedra-schematic.svg';
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
    flashHint('Exported sedra-schematic.svg');
}
async function copySchematicPng() {
    const markup = exportSchematicSvg();
    if (!markup) {
        flashHint('Nothing to export — the canvas is empty');
        return;
    }
    const bbox = schematicBBox();
    const w = (bbox[2] - bbox[0]) * 2, h = (bbox[3] - bbox[1]) * 2;
    const img = new Image();
    const url = URL.createObjectURL(new Blob([markup], { type: 'image/svg+xml' }));
    try {
        await new Promise((resolve, reject) => {
            img.onload = () => resolve();
            img.onerror = () => reject(new Error('SVG rasterisation failed'));
            img.src = url;
        });
        const canvas = document.createElement('canvas');
        canvas.width = w;
        canvas.height = h;
        const g2d = canvas.getContext('2d');
        g2d.drawImage(img, 0, 0, w, h);
        const blob = await new Promise(res => canvas.toBlob(res, 'image/png'));
        if (!blob)
            throw new Error('PNG encode failed');
        await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })]);
        notify('Schematic PNG copied to clipboard (2×)', 'info');
    }
    catch (err) {
        // Clipboard write needs a secure context / permission — fall back
        // to a download so the action never dead-ends.
        const a = document.createElement('a');
        a.href = url;
        a.download = 'sedra-schematic.svg';
        a.click();
        notify('Clipboard unavailable — downloaded SVG instead', 'warn');
    }
    finally {
        setTimeout(() => URL.revokeObjectURL(url), 5000);
    }
}
// ------------------------------------------------------------------
// Async affordance helpers: staged progress bars + busy buttons.
// ------------------------------------------------------------------
// frac in [0,1] shows the bar at that width; null hides it.
function setAsyncProgress(track, frac) {
    if (!track)
        return;
    if (frac === null) {
        track.hidden = true;
        return;
    }
    track.hidden = false;
    const fill = track.firstElementChild;
    if (fill)
        fill.style.width = `${Math.max(0, Math.min(100, Math.round(frac * 100)))}%`;
}
// Disabled + spinner. `disabled` (not just the class) so synthetic
// .click() calls and keyboard-triggered clicks are inert too.
function setBtnBusy(btn, busy) {
    btn.disabled = busy;
    btn.classList.toggle('busy', busy);
}
// ------------------------------------------------------------------
// Pop-up notifications
//
// `notify(msg, level)` appends a toast to the bottom-left stack:
//
//   * `info`  — blue accent, auto-dismisses after `INFO_AUTO_DISMISS_MS`.
//                Use for "thing-happened" status (drag committed,
//                paste placed, netlist copied).
//
//   * `warn`  — amber accent, sticky. Use for situations the user
//                should notice but isn't an error (drag reverted
//                because parity check failed, label conflict, etc.).
//
//   * `error` — red accent, sticky. Use for failures the user must
//                act on (parse error, file load failed).
//
// Sticky toasts grow an [×] close button. The stack reverses so the
// newest message lands at the top and older messages flow downward.
// A "Clear all" button hangs below the stack and removes every
// toast at once; it hides itself when the stack is empty.
// ------------------------------------------------------------------
const INFO_AUTO_DISMISS_MS = 3500;
function refreshClearAllButton() {
    const stack = document.getElementById('notifications');
    const btn = document.getElementById('notifications-clear-all');
    if (!btn)
        return;
    btn.hidden = stack.children.length === 0;
}
function notify(msg, level = 'info', action) {
    const stack = document.getElementById('notifications');
    const el = document.createElement('div');
    el.className = `notification notification-${level}`;
    el.setAttribute('role', level === 'info' ? 'status' : 'alert');
    const text = document.createElement('span');
    text.className = 'notification-text';
    text.textContent = msg;
    el.appendChild(text);
    const dismiss = () => {
        if (el.parentElement)
            el.parentElement.removeChild(el);
        refreshClearAllButton();
    };
    if (action) {
        const btn = document.createElement('button');
        btn.className = 'notification-action';
        btn.type = 'button';
        btn.textContent = action.label;
        btn.addEventListener('click', () => { dismiss(); action.fn(); });
        el.appendChild(btn);
    }
    if (level === 'info') {
        window.setTimeout(dismiss, action ? 6000 : INFO_AUTO_DISMISS_MS);
    }
    else {
        const close = document.createElement('button');
        close.className = 'notification-close';
        close.type = 'button';
        close.textContent = '×';
        close.setAttribute('aria-label', 'Dismiss');
        close.addEventListener('click', dismiss);
        el.appendChild(close);
    }
    stack.appendChild(el);
    refreshClearAllButton();
    return el;
}
document.getElementById('notifications-clear-all').addEventListener('click', () => {
    const stack = document.getElementById('notifications');
    while (stack.firstChild)
        stack.removeChild(stack.firstChild);
    refreshClearAllButton();
});
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
// Bring `state.wires` into canonical Steiner-T form: insert
// T-vertices where they're geometrically real (coalesce), fuse
// adjacent collinear wires (merge), drop excessive collinear-only
// vertices (simplify), then propagate labels along connected
// components. Used by `pushHistory` (so committed snapshots are
// always canonical) and by `updateMove` (so the live drag preview
// shows the same canonical form the user will get on commit).
function canonicalizeWires() {
    coalesceJunctions();
    mergeOverlappingCollinearSegments();
    mergeCollinearWires();
    simplifyCollinearVertices();
    propagateLabels();
}
// Slimmer canonicalisation for the mid-drag preview: skip merge
// (it's confused by the diagonal direct-connect lines that
// `wire-stretch` produces, and visually fusing wires while the
// user is actively dragging is jarring) and skip propagate (label
// propagation is a structural commit, not a per-frame thing). The
// remaining passes — coalesce + simplify — are what removes stale
// T-vertices from bystander wires whose perpendicular neighbour
// just dragged away.
function canonicalizeWiresForDraft() {
    coalesceJunctions();
    simplifyCollinearVertices();
}
function pushHistory() {
    // Every committed state is canonical Steiner-T form: any wire that
    // touches another wire's vertex or a part terminal mid-segment has
    // that point split into a vertex on its own polyline. Doing this in
    // pushHistory means a forgotten coalesce at a call-site is impossible.
    canonicalizeWires();
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
    updateUndoRedoButtons();
    refreshErc();
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
const btnUndo = document.getElementById('btn-undo');
const btnRedo = document.getElementById('btn-redo');
// Reflect the history stack in the buttons (KiCad/Figma convention:
// a greyed Undo tells you there's nothing left to undo).
function updateUndoRedoButtons() {
    btnUndo.disabled = historyIdx <= 0;
    btnRedo.disabled = historyIdx >= editHistory.length - 1;
}
// Flash whatever an undo/redo touched: parts that appeared or moved
// relative to the pre-restore snapshot.
function diffFlash(before) {
    const changed = [];
    for (const pp of state.parts) {
        const sig = `${pp.x},${pp.y},${pp.rot},${pp.flip ? 1 : 0},${pp.value ?? ''}`;
        if (before.get(pp.id) !== sig)
            changed.push(pp.id);
    }
    markFlash(changed);
}
function partSigs() {
    return new Map(state.parts.map(pp => [pp.id, `${pp.x},${pp.y},${pp.rot},${pp.flip ? 1 : 0},${pp.value ?? ''}`]));
}
btnUndo.addEventListener('click', () => {
    const before = partSigs();
    if (historyIdx > 0 && restore(historyIdx - 1)) {
        diffFlash(before);
        refreshProps();
        render();
    }
    updateUndoRedoButtons();
});
btnRedo.addEventListener('click', () => {
    const before = partSigs();
    if (historyIdx < editHistory.length - 1 && restore(historyIdx + 1)) {
        diffFlash(before);
        refreshProps();
        render();
    }
    updateUndoRedoButtons();
});
document.getElementById('btn-clear').addEventListener('click', () => {
    const n = state.parts.length + state.wires.length;
    if (n === 0)
        return;
    state.parts = [];
    state.wires = [];
    state.nameCounters = {};
    state.selectedIds.clear();
    state.selectedSegments.clear();
    state.wireDraft = null;
    pushHistory();
    refreshProps();
    render();
    // No blocking confirm() — clearing is immediately undoable, and the
    // toast carries the undo affordance (Linear/Figma convention).
    notify(`Cleared ${n} item${n === 1 ? '' : 's'}`, 'info', {
        label: 'Undo',
        fn: () => document.getElementById('btn-undo').click(),
    });
});
document.getElementById('btn-fit').addEventListener('click', fitView);
document.getElementById('sb-grid').textContent = `Grid ${GRID}`;
// Suppress the inter-group divider on groups that lead a wrapped
// toolbar line (CSS alone can't detect flex wrap).
function markToolbarRowStarts() {
    for (const row of document.querySelectorAll('.toolbar-row')) {
        let prevTop = null;
        for (const g of row.querySelectorAll(':scope > .group')) {
            g.classList.toggle('row-start', prevTop !== null && g.offsetTop !== prevTop);
            prevTop = g.offsetTop;
        }
    }
}
markToolbarRowStarts();
window.addEventListener('resize', markToolbarRowStarts);
// Drag-options gear popover.
{
    const gear = document.getElementById('btn-drag-options');
    const pop = document.getElementById('drag-options-pop');
    const onPopKey = (ev) => {
        if (ev.key === 'Escape') {
            closePop();
            gear.focus();
            ev.preventDefault();
            ev.stopPropagation();
        }
    };
    const closePop = () => {
        pop.hidden = true;
        gear.setAttribute('aria-expanded', 'false');
        gear.classList.remove('active');
        document.removeEventListener('keydown', onPopKey, true);
        window.removeEventListener('resize', closePop);
        window.removeEventListener('blur', closePop);
    };
    gear.addEventListener('click', () => {
        if (!pop.hidden) {
            closePop();
            return;
        }
        const r = gear.getBoundingClientRect();
        pop.style.left = `${r.left}px`;
        pop.style.top = `${r.bottom + 4}px`;
        pop.hidden = false;
        gear.setAttribute('aria-expanded', 'true');
        gear.classList.add('active');
        document.addEventListener('keydown', onPopKey, true);
        window.addEventListener('resize', closePop);
        window.addEventListener('blur', closePop);
    });
    document.addEventListener('mousedown', (e) => {
        if (!pop.hidden && !pop.contains(e.target)
            && e.target !== gear && !gear.contains(e.target)) {
            closePop();
        }
    }, true);
}
// Collapsible side-panel sections, persisted per heading.
{
    const PANE_KEY = 'sycan.sedra.panes.v1';
    let collapsed = {};
    try {
        collapsed = JSON.parse(localStorage.getItem(PANE_KEY) || '{}');
    }
    catch (_) { /* fresh */ }
    const headers = document.querySelectorAll('#side h3');
    headers.forEach(h3 => {
        const pane = h3.nextElementSibling;
        if (!pane)
            return;
        const key = (h3.textContent || '').trim();
        const apply = () => {
            const isCollapsed = !!collapsed[key];
            h3.classList.toggle('collapsed', isCollapsed);
            pane.style.display = isCollapsed ? 'none' : '';
        };
        apply();
        h3.addEventListener('click', () => {
            collapsed[key] = !collapsed[key];
            try {
                localStorage.setItem(PANE_KEY, JSON.stringify(collapsed));
            }
            catch (_) { /* full */ }
            apply();
        });
    });
}
// ERC status-bar zone cycles through findings.
document.getElementById('sb-erc').addEventListener('click', () => {
    if (!ercCache.length)
        return;
    const f = ercCache[ercCycle % ercCache.length];
    ercCycle++;
    focusErcFinding(f);
});
// One-shot attention flash: a fading halo on parts affected by a
// paste / duplicate / undo / redo. render() rebuilds the SVG per
// frame, so the halo is recomputed from a timestamp map and a rAF
// loop keeps re-rendering until every flash expires.
const FLASH_MS = 600;
const flashParts = new Map(); // part id -> expiry ts
let flashRaf = 0;
function markFlash(ids) {
    const until = Date.now() + FLASH_MS;
    for (const id of ids) {
        if (state.parts.some(pp => pp.id === id))
            flashParts.set(id, until);
    }
    if (!flashRaf && flashParts.size) {
        const tick = () => {
            const now = Date.now();
            for (const [id, exp] of flashParts) {
                if (exp <= now)
                    flashParts.delete(id);
            }
            render();
            flashRaf = flashParts.size ? requestAnimationFrame(tick) : 0;
        };
        flashRaf = requestAnimationFrame(tick);
    }
}
function drawFlashHalos() {
    if (!flashParts.size)
        return;
    const now = Date.now();
    for (const [id, exp] of flashParts) {
        const pp = state.parts.find(x => x.id === id);
        if (!pp || exp <= now)
            continue;
        const [x0, y0, x1, y1] = partBBox(pp);
        el('rect', {
            x: x0 - 4, y: y0 - 4,
            width: x1 - x0 + 8, height: y1 - y0 + 8,
            rx: 4, ry: 4,
            class: 'flash-halo',
            opacity: String(((exp - now) / FLASH_MS * 0.5).toFixed(3)),
            'pointer-events': 'none',
        }, svg);
    }
}
// Empty-canvas starter card: quick orientation for first-time users,
// dismissed once and remembered.
const WELCOME_KEY = 'sycan.sedra.welcome.v1';
let starterCardEl = null;
function loadExampleCircuit() {
    state.parts = [];
    state.wires = [];
    state.nameCounters = {};
    state.nextId = 1;
    state.selectedIds.clear();
    state.selectedSegments.clear();
    // Voltage divider: V1 feeds R1 (series, horizontal) into R2 (shunt).
    // Wires meet every terminal end-on — no runs across part bodies.
    addPart('vsrc', 0, 80, 0); // V1: terminals (0,40),(0,120)
    const v1 = state.parts[state.parts.length - 1];
    v1.value = 'Vs';
    addPart('res', 160, -40, 90); // R1 horizontal: (120,-40),(200,-40)
    state.parts[state.parts.length - 1].value = '10k';
    addPart('res', 320, 80, 0); // R2 vertical: (320,40),(320,120)
    state.parts[state.parts.length - 1].value = '10k';
    addPart('gnd', 0, 240, 0);
    addPart('gnd', 320, 240, 0);
    state.wires.push({ id: `W${state.nextId++}`,
        points: [[0, 40], [0, -40], [120, -40]] });
    state.wires.push({ id: `W${state.nextId++}`,
        points: [[200, -40], [320, -40], [320, 40]] });
    state.wires.push({ id: `W${state.nextId++}`, points: [[0, 120], [0, 240]] });
    state.wires.push({ id: `W${state.nextId++}`, points: [[320, 120], [320, 240]] });
    pushHistory();
    setTool('select');
    fitView();
    refreshProps();
    flashHint('Example loaded — try Calc Node on the middle net');
}
function updateStarterCard() {
    const empty = !state.parts.length && !state.wires.length;
    let dismissed = false;
    try {
        dismissed = localStorage.getItem(WELCOME_KEY) === '1';
    }
    catch (_) { /* ok */ }
    const want = empty && !dismissed;
    if (!want) {
        if (starterCardEl) {
            starterCardEl.remove();
            starterCardEl = null;
        }
        return;
    }
    if (starterCardEl)
        return;
    const card = document.createElement('div');
    card.className = 'starter-card';
    card.innerHTML =
        '<button class="starter-close" type="button" aria-label="Dismiss">×</button>' +
            '<h2>Start a schematic</h2>' +
            '<ul>' +
            '<li><kbd>R</kbd> <kbd>L</kbd> <kbd>C</kbd> <kbd>V</kbd>… then click the grid to place</li>' +
            '<li><kbd>W</kbd> draws wires &middot; corners snap to Manhattan</li>' +
            '<li><kbd>Ctrl+K</kbd> command palette &middot; <kbd>?</kbd> all shortcuts</li>' +
            '</ul>' +
            '<button class="starter-example" type="button">Load an example circuit</button>';
    card.querySelector('.starter-close').addEventListener('click', () => {
        try {
            localStorage.setItem(WELCOME_KEY, '1');
        }
        catch (_) { /* ok */ }
        updateStarterCard();
    });
    card.querySelector('.starter-example').addEventListener('click', () => {
        loadExampleCircuit();
    });
    wrap.appendChild(card);
    starterCardEl = card;
}
// More-parts popover: long-tail component chips.
{
    const more = document.getElementById('btn-more-parts');
    const pop = document.getElementById('more-parts-pop');
    const onKey = (ev) => {
        if (ev.key === 'Escape') {
            closeMore();
            more.focus();
            ev.preventDefault();
            ev.stopPropagation();
        }
    };
    const closeMore = () => {
        pop.hidden = true;
        more.setAttribute('aria-expanded', 'false');
        document.removeEventListener('keydown', onKey, true);
        window.removeEventListener('resize', closeMore);
        window.removeEventListener('blur', closeMore);
    };
    more.addEventListener('click', () => {
        if (!pop.hidden) {
            closeMore();
            return;
        }
        const r = more.getBoundingClientRect();
        pop.style.left = `${r.left}px`;
        pop.style.top = `${r.bottom + 4}px`;
        pop.hidden = false;
        more.setAttribute('aria-expanded', 'true');
        document.addEventListener('keydown', onKey, true);
        window.addEventListener('resize', closeMore);
        window.addEventListener('blur', closeMore);
    });
    pop.addEventListener('click', (e) => {
        // Picking a tool chip closes the popover.
        if (e.target.closest('.tool[data-tool]'))
            closeMore();
    });
    document.addEventListener('mousedown', (e) => {
        if (!pop.hidden && !pop.contains(e.target)
            && e.target !== more && !more.contains(e.target)) {
            closeMore();
        }
    }, true);
}
// Toolbar Calc-arm button mirrors the side-panel Calc Node button.
document.getElementById('btn-calc-arm').addEventListener('click', () => {
    if (state.calcNode.armed)
        cancelCalcNodePick();
    else
        startCalcNodePick();
});
document.getElementById('sb-zoom-in').addEventListener('click', (e) => {
    setZoom(state.zoom * ZOOM_STEP);
    e.currentTarget.blur(); // keep Space = rotate
});
document.getElementById('sb-zoom-out').addEventListener('click', (e) => {
    setZoom(state.zoom / ZOOM_STEP);
    e.currentTarget.blur();
});
sbZoomPct.addEventListener('click', (e) => {
    setZoom(1);
    e.currentTarget.blur();
});
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
    fitBounds(xmin, ymin, xmax, ymax, 2);
}
function fitBounds(xmin, ymin, xmax, ymax, maxZoom) {
    const pad = 40;
    const W = wrap.clientWidth, H = wrap.clientHeight;
    const w = (xmax - xmin) + 2 * pad;
    const h = (ymax - ymin) + 2 * pad;
    state.zoom = Math.min(W / w, H / h, maxZoom);
    state.pan.x = -xmin * state.zoom + (W - (xmax - xmin) * state.zoom) / 2;
    state.pan.y = -ymin * state.zoom + (H - (ymax - ymin) * state.zoom) / 2;
    render();
}
// Shift+F. Segment-selected wires also mirror their wireId into
// selectedIds, so iterating selectedIds covers segment selections.
function zoomToSelection() {
    if (!state.selectedIds.size) {
        fitView();
        return;
    }
    let xmin = Infinity, ymin = Infinity, xmax = -Infinity, ymax = -Infinity;
    for (const pp of state.parts) {
        if (!state.selectedIds.has(pp.id))
            continue;
        const [x0, y0, x1, y1] = partBBox(pp);
        xmin = Math.min(xmin, x0);
        ymin = Math.min(ymin, y0);
        xmax = Math.max(xmax, x1);
        ymax = Math.max(ymax, y1);
    }
    for (const w of state.wires) {
        if (!state.selectedIds.has(w.id))
            continue;
        for (const [x, y] of w.points) {
            xmin = Math.min(xmin, x);
            ymin = Math.min(ymin, y);
            xmax = Math.max(xmax, x);
            ymax = Math.max(ymax, y);
        }
    }
    if (xmin === Infinity) {
        fitView();
        return;
    }
    fitBounds(xmin, ymin, xmax, ymax, ZOOM_MAX);
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
            selectAllParts();
            e.preventDefault();
            return;
        }
        if (k === 'd') {
            duplicateSelection();
            e.preventDefault();
            return;
        }
        if (k === 'k') {
            toggleCmdPalette();
            e.preventDefault();
            return;
        }
        if (k === '0') {
            setZoom(1);
            e.preventDefault();
            return;
        }
        if (k === '=' || k === '+') {
            setZoom(state.zoom * ZOOM_STEP);
            e.preventDefault();
            return;
        }
        if (k === '-' || k === '_') {
            setZoom(state.zoom / ZOOM_STEP);
            e.preventDefault();
            return;
        }
        return; // any other Ctrl/Cmd combo: don't intercept
    }
    // '?' toggles the shortcut cheat sheet.
    if (e.key === '?') {
        toggleShortcutOverlay();
        e.preventDefault();
        return;
    }
    // F2 opens the inline value editor on a single-part selection.
    if (e.key === 'F2') {
        if (state.selectedIds.size === 1) {
            const [onlyId] = state.selectedIds;
            openInlineValueEditor(onlyId);
        }
        e.preventDefault();
        return;
    }
    // Arrow keys nudge the selection one grid step, going through the
    // full move engine so attached wires follow per the drag settings.
    if (!e.altKey && state.tool === 'select' && !state.moveDraft
        && state.selectedIds.size
        && (e.key === 'ArrowLeft' || e.key === 'ArrowRight'
            || e.key === 'ArrowUp' || e.key === 'ArrowDown')) {
        const dx = e.key === 'ArrowLeft' ? -GRID
            : e.key === 'ArrowRight' ? GRID : 0;
        const dy = e.key === 'ArrowUp' ? -GRID
            : e.key === 'ArrowDown' ? GRID : 0;
        startMove([...state.selectedIds], [0, 0], 
        /*viaDrag=*/ false, /*freshlyPasted=*/ false);
        if (state.moveDraft) {
            updateMove([dx, dy]);
            commitMove();
        }
        e.preventDefault();
        return;
    }
    // Wire-draft power keys: Backspace pops the last corner, Enter
    // finishes, '/' flips the preview's first-leg posture.
    if (state.wireDraft) {
        if (e.key === 'Backspace') {
            const wd = state.wireDraft;
            // Pop back to the previous click point: corners were added in
            // L-pairs, so strip trailing collinear corners with the click.
            if (wd.points.length <= 1) {
                state.wireDraft = null;
                refreshHint();
            }
            else {
                wd.points.pop();
                // Drop a remaining auto-inserted L corner if present.
                const last = wd.points[wd.points.length - 1];
                const prev = wd.points[wd.points.length - 2];
                if (prev && last && (prev[0] === last[0] || prev[1] === last[1])
                    && wd.points.length >= 2 && (wd.points.length % 2 === 0)) {
                    // best-effort; canonicalize cleans any leftovers on commit
                }
            }
            render();
            e.preventDefault();
            return;
        }
        if (e.key === 'Enter') {
            finalizeWireDraft();
            e.preventDefault();
            return;
        }
        if (e.key === '/') {
            state.wireDraft.axisFirst =
                state.wireDraft.axisFirst === 'h' ? 'v' : 'h';
            render();
            e.preventDefault();
            return;
        }
    }
    // Tab / Shift+Tab cycles the selection through parts.
    if (e.key === 'Tab' && !e.ctrlKey && !e.metaKey && !e.altKey
        && state.tool === 'select' && !state.moveDraft && state.parts.length) {
        cycleSelection(e.shiftKey ? -1 : 1);
        e.preventDefault();
        return;
    }
    // 'Y' flips (mirrors) the selected parts about their own axis.
    if (k === 'y' && !e.altKey && state.selectedIds.size && !state.moveDraft) {
        flipSelection();
        e.preventDefault();
        return;
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
        expandSelectionToNet(firstKey.slice(0, firstKey.indexOf('|')));
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
        if (e.shiftKey && state.selectedIds.size)
            zoomToSelection();
        else
            fitView();
        e.preventDefault();
        return;
    }
    if (e.key === 'Escape') {
        if (cmdkEl) {
            closeCmdPalette();
            e.preventDefault();
            return;
        }
        if (ctxMenuEl) {
            closeContextMenu();
            e.preventDefault();
            return;
        }
        if (shortcutOverlayEl) {
            toggleShortcutOverlay();
            e.preventDefault();
            return;
        }
        if (selectGesture) {
            // Abort the in-flight gesture (cancels a live move-draft /
            // drops the marquee); the eventual mouseup is then inert.
            abortSelectGesture();
        }
        else if (state.calcNode.armed) {
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
            rotateSelection();
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
            rot: p.rot, flip: p.flip, value: p.value, ctrlSrc: p.ctrlSrc,
            params: p.params,
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
function pasteClipboard(at) {
    const np = clipboard.parts ? clipboard.parts.length : 0;
    const nw = clipboard.wires ? clipboard.wires.length : 0;
    if (np + nw === 0)
        return;
    // Anchor at the given point (context menu's "Paste here") or the
    // current cursor (snapped). If the user hasn't put the cursor over
    // the canvas yet, anchor a couple of cells off the origin so
    // duplicates don't stack invisibly.
    let anchor = snapPt(at ?? state.cursorWorld);
    if (!anchor[0] && !anchor[1])
        anchor = [GRID * 2, GRID * 2];
    const newIds = new Set();
    for (const c of clipboard.parts || []) {
        addPart(c.type, anchor[0] + c.dx, anchor[1] + c.dy, c.rot);
        const fresh = state.parts[state.parts.length - 1];
        if (c.value && c.type !== 'gnd')
            fresh.value = c.value;
        if (c.flip)
            fresh.flip = true;
        if (c.ctrlSrc)
            fresh.ctrlSrc = c.ctrlSrc;
        if (c.params)
            fresh.params = c.params;
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
// ------------------------------------------------------------------
// Drag-mode attachment classification, following KiCad's
// getConnectedDragItems (eeschema/tools/sch_move_tool.cpp):
//
//   1. Collect every *moving anchor point*: the terminals of selected
//      parts plus the endpoints of explicitly-moving wires.
//   2. For each unselected wire, flag the endpoints that sit on a
//      moving anchor (KiCad STARTPOINT / ENDPOINT). Both endpoints
//      flagged → the wire translates rigidly (`wire-captured`);
//      exactly one → it stretches at that end (`wire-stretch`).
//   3. A moving anchor with **two or more fixed connections** acts
//      like KiCad's "unselected junction at the drag point"
//      (ptHasUnselectedJunction): the attached wires stay anchored
//      and a single new zero-length stub wire is spawned to bridge
//      the moving anchor back to the junction. The same stub spawns
//      when the anchor sits on a fixed part's terminal or lands on
//      the *body* of a fixed wire (interior vertex / mid-segment) —
//      KiCad's SCH_SYMBOL / SCH_JUNCTION makeNewWire cases. The stub
//      is what preserves connectivity to non-wire anchors by
//      construction.
//
// Spawned stubs are created *after* startMove's pre-drag snapshot,
// so cancel / parity-revert removes them wholesale.
// ------------------------------------------------------------------
function classifyDragAttachments(effectiveIds, origs) {
    // 1. Moving anchor points.
    const movingPts = new Set();
    for (const id of effectiveIds) {
        const p = state.parts.find(pp => pp.id === id);
        if (p) {
            for (const t of partTerminals(p)) {
                movingPts.add(`${t.pos[0]},${t.pos[1]}`);
            }
            continue;
        }
        const w = state.wires.find(ww => ww.id === id);
        if (w && w.points.length >= 2) {
            const first = w.points[0];
            const last = w.points[w.points.length - 1];
            movingPts.add(`${first[0]},${first[1]}`);
            movingPts.add(`${last[0]},${last[1]}`);
        }
    }
    if (!movingPts.size)
        return;
    // 2. Census of *fixed* connections at each moving anchor:
    // endpoint incidences of unselected wires, touches on unselected
    // wire bodies (interior vertices / strictly-mid-segment), and
    // unselected part terminals.
    const fixedWires = state.wires.filter(w => !origs.has(w.id) && !w.bad && w.points.length >= 2);
    const selectedIdSet = new Set(effectiveIds);
    const endCount = new Map();
    const touchCount = new Map();
    const termCount = new Map();
    // First-leg orientation for stubs spawned at a wire-body touch:
    // perpendicular to the touched segment (KiCad stores the touched
    // line's angle + 90° on the new stub).
    const touchPerpAxis = new Map();
    const bump = (m, k) => m.set(k, (m.get(k) || 0) + 1);
    for (const w of fixedWires) {
        const first = w.points[0];
        const last = w.points[w.points.length - 1];
        for (const pt of [first, last]) {
            const k = `${pt[0]},${pt[1]}`;
            if (movingPts.has(k))
                bump(endCount, k);
        }
        for (let i = 1; i < w.points.length - 1; i++) {
            const k = `${w.points[i][0]},${w.points[i][1]}`;
            if (movingPts.has(k)) {
                bump(touchCount, k);
                const a = w.points[i - 1], b = w.points[i];
                if (!touchPerpAxis.has(k)) {
                    touchPerpAxis.set(k, a[1] === b[1] ? 'v' : 'h');
                }
            }
        }
        for (const key of movingPts) {
            const comma = key.indexOf(',');
            const x = Number(key.slice(0, comma));
            const y = Number(key.slice(comma + 1));
            for (let i = 0; i < w.points.length - 1; i++) {
                const a = w.points[i], b = w.points[i + 1];
                // Strictly inside an axis-aligned segment (vertices counted
                // above).
                const insideH = a[1] === b[1] && y === a[1]
                    && x > Math.min(a[0], b[0]) && x < Math.max(a[0], b[0]);
                const insideV = a[0] === b[0] && x === a[0]
                    && y > Math.min(a[1], b[1]) && y < Math.max(a[1], b[1]);
                if (insideH || insideV) {
                    bump(touchCount, key);
                    if (!touchPerpAxis.has(key)) {
                        touchPerpAxis.set(key, insideH ? 'v' : 'h');
                    }
                }
            }
        }
    }
    for (const p of state.parts) {
        if (selectedIdSet.has(p.id))
            continue;
        for (const t of partTerminals(p)) {
            const k = `${t.pos[0]},${t.pos[1]}`;
            if (movingPts.has(k))
                bump(termCount, k);
        }
    }
    const fixedConn = (k) => (endCount.get(k) || 0) + (touchCount.get(k) || 0) +
        (termCount.get(k) || 0);
    // ≥2 fixed connections = implicit junction: wires stay anchored
    // there, a stub bridges back to the moving anchor (KiCad's
    // ptHasUnselectedJunction rule).
    const isJunctionPt = (k) => fixedConn(k) >= 2;
    // 3. Endpoint flags → captured / stretch wires.
    for (const w of fixedWires) {
        const first = w.points[0];
        const last = w.points[w.points.length - 1];
        const kf = `${first[0]},${first[1]}`;
        const kl = `${last[0]},${last[1]}`;
        const startMoves = movingPts.has(kf) && !isJunctionPt(kf);
        const endMoves = movingPts.has(kl) && !isJunctionPt(kl);
        if (!startMoves && !endMoves)
            continue;
        const points = w.points.map(pt => [pt[0], pt[1]]);
        if (startMoves && endMoves) {
            origs.set(w.id, { kind: 'wire-captured', points });
        }
        else {
            const insideEnd = startMoves ? 'start' : 'end';
            const a = startMoves ? w.points[0] : w.points[w.points.length - 1];
            const b = startMoves ? w.points[1] : w.points[w.points.length - 2];
            const axisHint = a[0] === b[0] && a[1] !== b[1] ? 'v' :
                a[1] === b[1] && a[0] !== b[0] ? 'h' : null;
            origs.set(w.id, { kind: 'wire-stretch', points, insideEnd, axisHint });
        }
    }
    // 4. Stub spawning — one per moving anchor whose fixed connections
    // aren't already carried along by a stretched / captured wire end.
    for (const key of movingPts) {
        const needsStub = isJunctionPt(key) ||
            (touchCount.get(key) || 0) > 0 ||
            (termCount.get(key) || 0) > 0;
        if (!needsStub || fixedConn(key) === 0)
            continue;
        const comma = key.indexOf(',');
        const x = Number(key.slice(0, comma));
        const y = Number(key.slice(comma + 1));
        const id = `W${state.nextId++}`;
        state.wires.push({ id, points: [[x, y], [x, y]] });
        origs.set(id, {
            kind: 'wire-stretch',
            points: [[x, y], [x, y]],
            insideEnd: 'start',
            axisHint: touchPerpAxis.get(key) ?? null,
        });
    }
}
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
    // Snapshot every wire-endpoint grid cell that was already a free
    // dangle pre-drag, so `commitMove` can tell user-authored dangles
    // (left untouched) from auto-router artefacts (trimmed). Compute
    // before any partial-segment splitting below — splits introduce
    // transient new endpoints we don't want in this set.
    const preFreeEndpoints = freeEndpointKeys();
    // Selected device-terminal coordinates — fed into the segment
    // split so a vertex sitting on a selected device's terminal also
    // counts as "selected" for boundary classification. That's how
    // an unselected segment that bridges a selected device and a
    // selected segment via a single corner gets promoted to 'moving'
    // (sandwich rule) instead of leaving the device dangling on a
    // diagonal direct-connect line.
    const selectedTerminalKeys = new Set();
    if (!freshlyPasted) {
        for (const id of ids) {
            const p = state.parts.find(pp => pp.id === id);
            if (!p)
                continue;
            for (const t of partTerminals(p)) {
                selectedTerminalKeys.add(`${t.pos[0]},${t.pos[1]}`);
            }
        }
    }
    // Segment-level drag: split any partially-selected wire into
    // moving / boundary / fixed pieces.
    //   * Moving pieces translate by the delta (kind 'wire').
    //   * Boundary pieces stretch at their moving end (kind
    //     'wire-stretch' — live Manhattan bend, commits as shown).
    //   * Fixed pieces stay put and don't appear in the move-draft.
    const splitResult = freshlyPasted
        ? { movingIds: ids, boundaries: [] }
        : splitPartialWires(ids, selectedTerminalKeys);
    const effectiveIds = splitResult.movingIds;
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
    // Boundary pieces from partial-selection split → stretch wires.
    for (const b of splitResult.boundaries) {
        const wire = state.wires.find(w => w.id === b.wireId);
        if (!wire)
            continue;
        origs.set(b.wireId, {
            kind: 'wire-stretch',
            points: wire.points.map(pt => [pt[0], pt[1]]),
            insideEnd: b.insideEnd,
            axisHint: b.axisHint,
        });
    }
    if (!origs.size)
        return;
    // Drag-mode attachment capture, following KiCad's
    // getConnectedDragItems (eeschema/tools/sch_move_tool.cpp). Off for
    // paste-placement (a fresh paste's wires aren't connected to the
    // surrounding circuit yet, so there's nothing to capture).
    const dragMode = !freshlyPasted &&
        document.getElementById('drag-mode')?.checked === true;
    if (dragMode) {
        classifyDragAttachments(effectiveIds, origs);
    }
    // Optional pre-drag connectivity snapshot for the parity check.
    const parityOn = document.getElementById('parity-check')?.checked === true;
    const paritySig = (dragMode && parityOn) ? netSignature() : null;
    const noRevert = document.getElementById('no-revert')?.checked === true;
    state.moveDraft = {
        ids: [...effectiveIds],
        origs,
        pickup: [pickup[0], pickup[1]],
        delta: [0, 0],
        viaDrag,
        freshlyPasted,
        dragMode,
        parityCheck: parityOn,
        noRevert,
        paritySig,
        preDragSnapshot,
        preFreeEndpoints,
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
        else if (orig.kind === 'wire-stretch') {
            const wire = state.wires.find(w => w.id === id);
            if (!wire)
                continue;
            // KiCad-style live orthogonal stretch: the attached endpoint
            // follows the drag, a bend vertex keeps the path Manhattan,
            // and the rest of the wire stays anchored. What you see during
            // the drag is exactly what commits — there is no re-routing
            // pass afterwards.
            wire.points = stretchWirePoints(orig.points, orig.insideEnd, dx, dy, orig.axisHint);
        }
    }
    // Canonicalise the live state so the semi-transparent draft
    // shows the same junction layout the user will see on commit:
    // stale T-vertices that the dragged-away wire used to anchor
    // disappear, and any new T-junction the moved geometry creates
    // is materialised as an explicit vertex (which `drawJunctions`
    // then renders as a dot). No history write — `pushHistory` runs
    // exactly once per drag, in `commitMove`, so undo collapses the
    // whole drag sequence into a single Ctrl+Z.
    canonicalizeWiresForDraft();
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
    // Drag-mode commit (KiCad model): stretched wires kept every
    // pre-drag connection *by construction*, so there is no routing
    // step, no retry loop, and no router-failure revert. Normalise
    // junctions first so the netlist DSU sees any T-junction the moved
    // geometry created, then run the optional parity check — a
    // mismatch now always means the user physically landed moved
    // geometry on a previously-unrelated net (a new junction / short),
    // never an internal failure.
    if (md.dragMode && (md.delta[0] !== 0 || md.delta[1] !== 0)) {
        coalesceJunctions();
        if (md.parityCheck && md.paritySig !== null
            && netSignature() !== md.paritySig) {
            if (md.noRevert) {
                notify('Drag changed net connectivity — moved geometry landed on ' +
                    'another net. Committed anyway (no-revert); check the new ' +
                    'junctions or undo.', 'warn');
            }
            else {
                // Restore from the pre-drag snapshot — startMove may have
                // physically split partially-selected wires and spawned
                // stretch stubs, so the origs map alone can't undo all of it.
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
                notify('Drag reverted — it would have changed net connectivity ' +
                    '(moved geometry landed on another net). Uncheck Parity or ' +
                    'check No-revert to commit such drags.', 'warn');
                return;
            }
        }
        // Cleanup, mirroring KiCad's post-drag trimDanglingLines +
        // schematic cleanup: drop wires the stretch collapsed to zero
        // length, restore canonical Steiner-T form, then trim endpoints
        // that became dangling *because of this drag* (pre-existing
        // user-authored dangles stay untouched).
        dropZeroLengthWires();
        canonicalizeWires();
        trimNewDangles(md.preFreeEndpoints);
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
        markFlash(md.ids);
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
    document.getElementById('btn-calc-arm')?.classList.add('active');
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
    document.getElementById('btn-calc-arm')?.classList.remove('active');
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
    const calcProgress = document.getElementById('calc-progress');
    setBtnBusy(calcBtn, true);
    try {
        const py = await ensureSycan((s, frac) => {
            calcLog(s);
            setAsyncProgress(calcProgress, frac);
        });
        const mode = pickAnalysisMode(info.text);
        calcLog(`Solving (${mode})…`);
        setAsyncProgress(calcProgress, 0.9);
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
    finally {
        setBtnBusy(calcBtn, false);
        setAsyncProgress(calcProgress, null);
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
        onStatus('Loading Python runtime… (1/4, one-time ~15s)', 0.08);
        // pyodide.js script tag is loaded async — wait for it.
        let waited = 0;
        while (typeof loadPyodide !== 'function') {
            if (waited > 30000)
                throw new Error('pyodide.js failed to load');
            await new Promise(r => setTimeout(r, 100));
            waited += 100;
        }
        const py = await loadPyodide();
        onStatus('Installing sympy… (2/4)', 0.45);
        await py.loadPackage(['sympy', 'micropip']);
        onStatus('Installing sycan… (3/4)', 0.75);
        await py.runPythonAsync(`
import micropip
await micropip.install('../repl/sycan-0.1.8-py3-none-any.whl')
import sycan, sympy
print('sycan ready (sympy', sympy.__version__, ')')
`);
        onStatus('Ready', 1);
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
    setTool('select');
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
// Set by the matrix-viewer's hover handler; read by render() →
// drawMatrixPartHighlight() to paint the schematic-side outline.
let matrixHighlightPartIds = new Set();
function drawMatrixPartHighlight() {
    if (matrixHighlightPartIds.size === 0)
        return;
    for (const p of state.parts) {
        if (!matrixHighlightPartIds.has(p.id))
            continue;
        const [x0, y0, x1, y1] = partBBox(p);
        const pad = 2;
        el('rect', {
            x: x0 - pad, y: y0 - pad,
            width: (x1 - x0) + 2 * pad,
            height: (y1 - y0) + 2 * pad,
            class: 'matrix-part-highlight',
            rx: 3, ry: 3,
        }, svg);
    }
}
let _matrixData = null;
let _matrixDataPromise = null;
// Run sycan in Pyodide and return per-component stamp coverage.
//
// Implementation: build the full MNA system once for shape and labels,
// then for each component re-stamp it onto a fresh zero matrix and
// snapshot which (i,j) entries became non-zero. This is the simplest
// reliable per-component attribution that doesn't require monkey-
// patching sympy's matrix; it costs O(N_components × stamp_time),
// which is small for editor-sized circuits.
async function computeMatrixData(py, netlistText) {
    py.globals.set('SEDRA_NETLIST', netlistText);
    const json = await py.runPythonAsync(`
import json
from sycan import parse
from sycan.mna import build_mna, StampContext
import sycan.cas as cas

_result = None
try:
    circuit = parse(SEDRA_NETLIST)
    A_full, x, b_full = build_mna(circuit, mode='dc')
    size = A_full.shape[0]
    nodes = list(circuit.nodes)
    n = len(nodes)
    flat = list(circuit.flat_components())
    aux_owners = [c for c in flat if c.aux_count('dc') > 0]
    node_rows = {name: idx - 1 for name, idx in circuit._nodes.items()}
    aux_rows = {c.name: n + k for k, c in enumerate(aux_owners)}
    labels = list(nodes) + [c.name for c in aux_owners]

    # Per-component stamp coverage. We re-run each component's stamp()
    # on a fresh zero (A,b) and snapshot which cells went non-zero.
    cells = {}    # (row, col) -> list[component_id]
    part_to_cells = {}
    for c in flat:
        A = cas.zeros(size, size)
        b = cas.zeros(size, 1)
        ctx = StampContext(
            A=A, b=b, node_rows=node_rows, aux_rows=aux_rows, mode='dc',
        )
        try:
            c.stamp(ctx)
        except Exception:
            continue
        comp_cells = []
        for i in range(size):
            for j in range(size):
                if A[i, j] != 0:
                    key = (i, j)
                    cells.setdefault(key, []).append(c.name)
                    comp_cells.append(key)
            if b[i] != 0:
                key = (i, size)  # b lives in the column past A
                cells.setdefault(key, []).append(c.name)
                comp_cells.append(key)
        if comp_cells:
            part_to_cells.setdefault(c.name, []).extend(comp_cells)

    # Pack as a list of cells with stable ordering (row-major).
    cell_list = []
    for (i, j) in sorted(cells.keys()):
        cell_list.append({'row': i, 'col': j, 'parts': cells[(i, j)]})
    # Re-index part_to_cells to point into cell_list rather than (i,j) tuples.
    cell_index = {(c['row'], c['col']): k for k, c in enumerate(cell_list)}
    part_to_cell_idx = {}
    for cname, keys in part_to_cells.items():
        seen = set()
        out = []
        for key in keys:
            if key in cell_index:
                k = cell_index[key]
                if k not in seen:
                    seen.add(k)
                    out.append(k)
        part_to_cell_idx[cname] = out

    _result = {
        'size': size,
        'labels': labels,
        'cells': cell_list,
        'partToCells': part_to_cell_idx,
        'nNodes': n,
    }
except Exception as e:
    _result = {'error': f'{type(e).__name__}: {e}'}
json.dumps(_result)
`);
    const parsed = JSON.parse(json);
    if (parsed.error) {
        throw new Error(parsed.error);
    }
    parsed.netlistDigest = netlistText;
    return parsed;
}
let _matrixEls = null;
function getMatrixEls() {
    if (_matrixEls)
        return _matrixEls;
    const panel = document.getElementById('matrix-viewer');
    const body = document.getElementById('matrix-viewer-body');
    const svgEl = document.getElementById('matrix-viewer-svg');
    const status = document.getElementById('matrix-viewer-status');
    const refreshBtn = document.getElementById('matrix-viewer-refresh');
    const closeBtn = document.getElementById('matrix-viewer-close');
    const header = document.getElementById('matrix-viewer-header');
    const title = document.getElementById('matrix-viewer-title');
    if (!panel || !body || !svgEl || !status || !refreshBtn ||
        !closeBtn || !header || !title)
        return null;
    _matrixEls = { panel, body, svg: svgEl, status, refreshBtn, closeBtn, header, title };
    return _matrixEls;
}
function setMatrixStatus(msg) {
    const els = getMatrixEls();
    if (els)
        els.status.textContent = msg;
}
// Layout the dot matrix into the SVG. Pure-DOM rendering; no React.
// `data` is produced by computeMatrixData(); `viewerSize` is the
// width/height of the panel body (we receive it explicitly so the
// render path stays decoupled from the DOM-measurement timing).
function renderMatrixDots(data, viewerSize) {
    const els = getMatrixEls();
    if (!els)
        return;
    const svgEl = els.svg;
    while (svgEl.firstChild)
        svgEl.removeChild(svgEl.firstChild);
    const cols = data.size + 1; // +1 for the b column
    const rows = data.size;
    if (rows === 0 || cols === 0) {
        svgEl.setAttribute('viewBox', '0 0 1 1');
        return;
    }
    // Layout constants. The label gutter on the left/top is sized in
    // viewBox units so it scales with the rest of the matrix; we pick
    // it as a fraction of the per-cell pitch so the labels stay legible
    // even when the panel shrinks.
    const labelGutter = 38; // px reserved for row/col labels
    const padding = 6;
    const innerW = Math.max(1, viewerSize.w - labelGutter - 2 * padding);
    const innerH = Math.max(1, viewerSize.h - labelGutter - 2 * padding);
    const cellPitch = Math.min(innerW / cols, innerH / rows);
    const dotR = Math.max(1.0, cellPitch * 0.32);
    const gridW = cellPitch * cols;
    const gridH = cellPitch * rows;
    const x0 = padding + labelGutter;
    const y0 = padding + labelGutter;
    // viewBox spans the whole panel body so dots, labels, and dividers
    // align with the visible window pixels.
    svgEl.setAttribute('viewBox', `0 0 ${viewerSize.w} ${viewerSize.h}`);
    // Faint grid (rows + cols).
    const gridLayer = el('g', { id: 'matrix-grid' }, svgEl);
    for (let i = 0; i <= rows; i++) {
        const y = y0 + i * cellPitch;
        el('line', {
            x1: x0, y1: y, x2: x0 + gridW, y2: y,
            class: 'matrix-grid-line',
        }, gridLayer);
    }
    for (let j = 0; j <= cols; j++) {
        const x = x0 + j * cellPitch;
        el('line', {
            x1: x, y1: y0, x2: x, y2: y0 + gridH,
            class: 'matrix-grid-line',
        }, gridLayer);
    }
    // Divider between A's node block and aux block (rows + cols).
    if (data.nNodes > 0 && data.nNodes < data.size) {
        const xMid = x0 + data.nNodes * cellPitch;
        const yMid = y0 + data.nNodes * cellPitch;
        el('line', {
            x1: x0, y1: yMid, x2: x0 + gridW, y2: yMid,
            class: 'matrix-divider',
        }, gridLayer);
        el('line', {
            x1: xMid, y1: y0, x2: xMid, y2: y0 + gridH,
            class: 'matrix-divider',
        }, gridLayer);
    }
    // Divider between A and the b column.
    {
        const xRhs = x0 + data.size * cellPitch;
        el('line', {
            x1: xRhs, y1: y0, x2: xRhs, y2: y0 + gridH,
            class: 'matrix-divider',
        }, gridLayer);
    }
    // Axis labels. Rows: V(node)/I(comp). Cols: same labels then "b".
    // Drop the labels when the cell pitch is too small to host the text
    // without overlap; we compare against a font-size cushion.
    const labelLayer = el('g', { id: 'matrix-labels' }, svgEl);
    const showLabels = cellPitch >= 9;
    if (showLabels) {
        for (let i = 0; i < rows; i++) {
            const y = y0 + (i + 0.5) * cellPitch + 3;
            el('text', {
                x: x0 - 3, y,
                class: 'matrix-axis-text',
                'text-anchor': 'end',
                'data-row-label': String(i),
            }, labelLayer).textContent = data.labels[i] ?? '?';
        }
        for (let j = 0; j < cols; j++) {
            const x = x0 + (j + 0.5) * cellPitch;
            const lab = j < data.size ? (data.labels[j] ?? '?') : 'b';
            const yLab = y0 - 4;
            const t = el('text', {
                x, y: yLab,
                class: 'matrix-axis-text',
                'text-anchor': 'start',
                transform: `rotate(-60 ${x} ${yLab})`,
                'data-col-label': String(j),
            }, labelLayer);
            t.textContent = lab;
        }
    }
    // Dots layer (one circle per populated cell) plus an over-cell hit
    // rect so hover events stay reliable even at tiny dotR.
    const dotsLayer = el('g', { id: 'matrix-dots' }, svgEl);
    const hitsLayer = el('g', { id: 'matrix-hits' }, svgEl);
    for (let k = 0; k < data.cells.length; k++) {
        const cell = data.cells[k];
        const cx = x0 + (cell.col + 0.5) * cellPitch;
        const cy = y0 + (cell.row + 0.5) * cellPitch;
        el('circle', {
            cx, cy, r: dotR,
            class: 'matrix-dot',
            'data-cell-idx': String(k),
            'data-row': String(cell.row),
            'data-col': String(cell.col),
        }, dotsLayer);
    }
    // Hit rectangles cover *every* row × col cell (whether populated or
    // not) so the user can hover an empty cell to see "this row is
    // V(out), this col is I(V1)" via the axis-label highlight.
    for (let i = 0; i < rows; i++) {
        for (let j = 0; j < cols; j++) {
            el('rect', {
                x: x0 + j * cellPitch, y: y0 + i * cellPitch,
                width: cellPitch, height: cellPitch,
                class: 'matrix-cell-hit',
                'data-row': String(i),
                'data-col': String(j),
            }, hitsLayer);
        }
    }
}
// Apply hover state across both the matrix viewer (highlight peer
// cells, axis labels) and the schematic (outline originating parts).
// Called from the cell hit-rect mouseenter/mouseleave handlers.
function applyMatrixCellHover(data, row, col, entering) {
    const els = getMatrixEls();
    if (!els)
        return;
    const svgEl = els.svg;
    // Reset transient classes.
    svgEl.querySelectorAll('.matrix-dot-hover, .matrix-dot-related').forEach(n => {
        n.classList.remove('matrix-dot-hover');
        n.classList.remove('matrix-dot-related');
    });
    svgEl.querySelectorAll('.matrix-axis-active').forEach(n => {
        n.classList.remove('matrix-axis-active');
    });
    matrixHighlightPartIds = new Set();
    if (!entering) {
        setMatrixStatus(`${data.size} × ${data.size + 1} (incl. b)`);
        render();
        return;
    }
    // Find the cell record under the cursor (if any).
    let cellIdx = -1;
    for (let k = 0; k < data.cells.length; k++) {
        if (data.cells[k].row === row && data.cells[k].col === col) {
            cellIdx = k;
            break;
        }
    }
    const rowLabel = data.labels[row] ?? '?';
    const colLabel = col < data.size ? (data.labels[col] ?? '?') : 'b';
    // Activate the row/col axis labels.
    const rl = svgEl.querySelector(`text[data-row-label="${row}"]`);
    if (rl)
        rl.classList.add('matrix-axis-active');
    const cl = svgEl.querySelector(`text[data-col-label="${col}"]`);
    if (cl)
        cl.classList.add('matrix-axis-active');
    if (cellIdx >= 0) {
        const cell = data.cells[cellIdx];
        // Hovered dot itself.
        const hovered = svgEl.querySelector(`circle[data-cell-idx="${cellIdx}"]`);
        if (hovered)
            hovered.classList.add('matrix-dot-hover');
        // Sibling dots from the same component(s).
        const peers = new Set();
        for (const partId of cell.parts) {
            const list = data.partToCells[partId];
            if (!list)
                continue;
            for (const k of list)
                peers.add(k);
        }
        peers.delete(cellIdx);
        for (const k of peers) {
            const c = svgEl.querySelector(`circle[data-cell-idx="${k}"]`);
            if (c)
                c.classList.add('matrix-dot-related');
        }
        matrixHighlightPartIds = new Set(cell.parts);
        const partList = cell.parts.join(', ');
        setMatrixStatus(`(${rowLabel}, ${colLabel})  ←  ${partList}`);
    }
    else {
        setMatrixStatus(`(${rowLabel}, ${colLabel})  ←  (empty)`);
    }
    render();
}
// Wire the SVG hit rects to the hover machinery. Called once per
// renderMatrixDots() pass — listeners are attached afresh on each
// rebuild because the DOM nodes are recreated.
function wireMatrixHover(data) {
    const els = getMatrixEls();
    if (!els)
        return;
    const svgEl = els.svg;
    const hits = svgEl.querySelectorAll('.matrix-cell-hit');
    hits.forEach(h => {
        const row = Number(h.getAttribute('data-row'));
        const col = Number(h.getAttribute('data-col'));
        h.addEventListener('mouseenter', () => applyMatrixCellHover(data, row, col, true));
        h.addEventListener('mouseleave', () => applyMatrixCellHover(data, row, col, false));
    });
}
function rerenderMatrixViewer() {
    if (!_matrixData)
        return;
    const els = getMatrixEls();
    if (!els)
        return;
    const rect = els.body.getBoundingClientRect();
    const w = Math.max(1, Math.floor(rect.width));
    const h = Math.max(1, Math.floor(rect.height));
    renderMatrixDots(_matrixData, { w, h });
    wireMatrixHover(_matrixData);
}
// Wrap computeMatrixData so concurrent button mashes don't spawn
// duplicate Pyodide calls. The promise resolves with the freshest
// data; if a second refresh fires while one is in flight, the second
// request reuses the in-flight promise.
async function refreshMatrixData() {
    const els = getMatrixEls();
    if (!els)
        return;
    const nl = buildNetlist();
    if (_matrixDataPromise) {
        // Already running — let the in-flight call finish first.
        return;
    }
    const matrixProgress = document.getElementById('matrix-progress');
    setMatrixStatus('Loading sycan…');
    _matrixDataPromise = (async () => {
        const py = await ensureSycan((s, frac) => {
            setMatrixStatus(s);
            setAsyncProgress(matrixProgress, frac);
        });
        setMatrixStatus('Building matrix…');
        setAsyncProgress(matrixProgress, 0.9);
        return await computeMatrixData(py, nl.text);
    })();
    try {
        const data = await _matrixDataPromise;
        _matrixData = data;
        setMatrixStatus(`${data.size} × ${data.size + 1} (incl. b)`);
        rerenderMatrixViewer();
    }
    catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        setMatrixStatus(`Error: ${msg}`);
    }
    finally {
        _matrixDataPromise = null;
        setAsyncProgress(document.getElementById('matrix-progress'), null);
    }
}
function openMatrixViewer() {
    const els = getMatrixEls();
    if (!els)
        return;
    els.panel.classList.remove('hidden');
    els.panel.setAttribute('aria-hidden', 'false');
    // First open: position inside the canvas area so the popup doesn't
    // cover the side panel's action row. The header drag switches to
    // left/top pinning, so this only applies until the user moves it.
    if (!els.panel.style.left) {
        const cw = document.getElementById('canvas-wrap').getBoundingClientRect();
        els.panel.style.left =
            `${Math.max(8, cw.right - els.panel.offsetWidth - 16)}px`;
        els.panel.style.top =
            `${Math.max(8, cw.bottom - els.panel.offsetHeight - 16)}px`;
        els.panel.style.right = 'auto';
        els.panel.style.bottom = 'auto';
    }
    refreshMatrixData();
}
function closeMatrixViewer() {
    const els = getMatrixEls();
    if (!els)
        return;
    els.panel.classList.add('hidden');
    els.panel.setAttribute('aria-hidden', 'true');
    matrixHighlightPartIds = new Set();
    render();
}
// Toolbar / panel button wiring. Header drag uses the same pattern as
// the side-panel resizer above (mousedown → document mousemove/up).
{
    const btn = document.getElementById('btn-matrix');
    if (btn) {
        btn.addEventListener('click', () => {
            const els = getMatrixEls();
            if (!els)
                return;
            if (els.panel.classList.contains('hidden'))
                openMatrixViewer();
            else
                closeMatrixViewer();
        });
    }
    const els = getMatrixEls();
    if (els) {
        els.closeBtn.addEventListener('click', closeMatrixViewer);
        els.refreshBtn.addEventListener('click', () => {
            // Force a recompute even if the netlist hasn't changed — useful
            // after toggling Drag/Parity options or when the user just
            // wants to re-run sycan.
            _matrixData = null;
            refreshMatrixData();
        });
        // Drag the panel by its header. We pin via top/left so the panel
        // can leave its bottom-right anchor; CSS `resize: both` keeps the
        // size-handle in the bottom-right corner regardless.
        let dragging = false;
        let startX = 0, startY = 0, startLeft = 0, startTop = 0;
        const onMove = (e) => {
            if (!dragging)
                return;
            const dx = e.clientX - startX;
            const dy = e.clientY - startY;
            const w = els.panel.offsetWidth;
            const h = els.panel.offsetHeight;
            let nx = startLeft + dx;
            let ny = startTop + dy;
            // Clamp to viewport so the header is always grabbable.
            nx = Math.max(8, Math.min(window.innerWidth - 40, nx));
            ny = Math.max(8, Math.min(window.innerHeight - 30, ny));
            els.panel.style.left = `${nx}px`;
            els.panel.style.top = `${ny}px`;
            els.panel.style.right = 'auto';
            els.panel.style.bottom = 'auto';
            // Keep the matrix scaled to the panel during drag (size doesn't
            // change but a rerender is cheap).
            e.preventDefault();
        };
        const onUp = () => {
            if (!dragging)
                return;
            dragging = false;
            document.removeEventListener('mousemove', onMove);
            document.removeEventListener('mouseup', onUp);
        };
        els.header.addEventListener('mousedown', (e) => {
            const me = e;
            // Don't drag-start on header-button clicks.
            if (me.target instanceof Element &&
                (me.target.tagName === 'BUTTON' ||
                    me.target.closest('button')))
                return;
            if (me.button !== 0)
                return;
            const rect = els.panel.getBoundingClientRect();
            startX = me.clientX;
            startY = me.clientY;
            startLeft = rect.left;
            startTop = rect.top;
            dragging = true;
            document.addEventListener('mousemove', onMove);
            document.addEventListener('mouseup', onUp);
            me.preventDefault();
        });
        // Watch the panel's body for size changes (CSS `resize: both`
        // doesn't fire any DOM event by itself) and re-layout the dot
        // grid each time. ResizeObserver fires synchronously with layout
        // so the grid always tracks the user's drag.
        if (typeof ResizeObserver !== 'undefined') {
            const ro = new ResizeObserver(() => {
                if (els.panel.classList.contains('hidden'))
                    return;
                rerenderMatrixViewer();
            });
            ro.observe(els.body);
        }
    }
}
init();
