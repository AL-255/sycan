"use strict";
// SEDRA embeddable viewer — view-only schematic renderer.
//
// Loaded by viewer.html together with glyphs.js (this project compiles
// with `module: "none"`, so glyphs.ts's top-level declarations —
// ELEM_TYPES, drawPart, partTerminals, partBBox, wireLabelAnchor, el,
// loadGlyphs, decodeCircuitB64 — are plain globals here). editor.js is
// NOT loaded: everything in this file is wrapped in one IIFE so no
// top-level identifier can collide with editor.ts inside the shared
// tsc program, and so nothing leaks into the page's global scope
// except the small `sedraViewer` debug/test handle.
//
// Usage (iframe embed on any website):
//
//   <iframe src="https://…/sedra/viewer.html#data=<base64>"
//           width="720" height="480"></iframe>
//
// The fragment carries a base64-encoded circuit document — the same
// JSON shape the editor's "Export JSON" writes ({version, parts,
// wires, nameCounters}), URL-safe alphabet, padding optional. The
// editor's "Copy embed code" / "Copy view-only link" commands produce
// these URLs. Accepted sources, in priority order:
//
//   #data=<b64>   or  #circuit=<b64>   or bare  #<b64>
//   ?d=<b64>      or  ?data=<b64>          (query fallback)
//
// Optional query parameters:
//   theme=light|dark   force a theme (default: follow the embedding
//                      page's prefers-color-scheme)
//   controls=0         hide the zoom/fit/open control cluster
//
// View-only means no editing — but the schematic is still alive:
// drag pans, wheel zooms about the cursor, double-click re-fits.
(() => {
    const wrapElMaybe = document.getElementById('viewer-root');
    if (!wrapElMaybe)
        return; // not the viewer page — nothing to do
    // Re-bound as non-null so the hoisted function declarations below see
    // the narrowed type (TS doesn't carry narrowing into them).
    const wrapEl = wrapElMaybe;
    const svgEl = document.getElementById('viewer-svg');
    const msgEl = document.getElementById('viewer-msg');
    const controlsEl = document.getElementById('viewer-controls');
    const openLink = document.getElementById('v-open');
    // ---- circuit + camera state --------------------------------------
    let vparts = [];
    let vwires = [];
    let vpan = { x: 0, y: 0 };
    let vzoom = 1;
    let userMoved = false; // once the user pans/zooms, resize stops re-fitting
    const VZOOM_MIN = 0.05;
    const VZOOM_MAX = 8;
    const VZOOM_STEP = Math.SQRT2;
    // ---- input decoding ----------------------------------------------
    function rawCircuitParam() {
        const h = (location.hash || '').replace(/^#/, '');
        if (h) {
            const m = /^(?:data=|circuit=)?([A-Za-z0-9_\-+/=]+)$/.exec(h);
            if (m)
                return m[1];
        }
        const q = new URLSearchParams(location.search);
        return q.get('d') || q.get('data') || null;
    }
    function applyPageOptions() {
        const q = new URLSearchParams(location.search);
        const theme = q.get('theme');
        if (theme === 'light' || theme === 'dark') {
            document.documentElement.dataset.theme = theme;
        }
        if (q.get('controls') === '0')
            controlsEl.hidden = true;
    }
    function showMessage(title, detail) {
        msgEl.innerHTML = '';
        const h = document.createElement('div');
        h.className = 'viewer-msg-title';
        h.textContent = title;
        const d = document.createElement('div');
        d.className = 'viewer-msg-detail';
        d.textContent = detail;
        msgEl.append(h, d);
        msgEl.hidden = false;
    }
    // ---- rendering ----------------------------------------------------
    // A trimmed-down copy of the editor's render(): wires, net labels,
    // parts with the connected-terminal census, junction dots. No grid,
    // no selection/hover/ERC/overlay layers — this is a figure, not a
    // workbench.
    function vWirePath(points) {
        if (!points.length)
            return '';
        let d = `M${points[0][0]},${points[0][1]}`;
        for (let i = 1; i < points.length; i++)
            d += ` L${points[i][0]},${points[i][1]}`;
        return d;
    }
    function renderViewer() {
        const W = wrapEl.clientWidth, H = wrapEl.clientHeight;
        svgEl.setAttribute('width', String(W));
        svgEl.setAttribute('height', String(H));
        svgEl.setAttribute('viewBox', `${-vpan.x / vzoom} ${-vpan.y / vzoom} ${W / vzoom} ${H / vzoom}`);
        while (svgEl.firstChild)
            svgEl.removeChild(svgEl.firstChild);
        // Layer 1: wires.
        for (const w of vwires) {
            if (!w.points || w.points.length < 2)
                continue;
            el('path', {
                d: vWirePath(w.points),
                class: w.bad ? 'wire wire-bad' : 'wire',
            }, svgEl);
        }
        // Layer 1.5: net-label tags — one per label, riding the labelled
        // wire with the longest segment (same-name wires share one tag).
        drawViewerNetLabels();
        // Layer 2: parts. Connected-terminal census mirrors the editor:
        // a terminal covered by a wire vertex/segment lattice point or by
        // another part's terminal draws nothing; open pin-ends get a ring.
        const occupied = new Set();
        for (const w of vwires) {
            if (w.bad || !w.points || w.points.length < 2)
                continue;
            for (const pt of w.points)
                occupied.add(`${pt[0]},${pt[1]}`);
            for (let i = 0; i < w.points.length - 1; i++) {
                const a = w.points[i], b = w.points[i + 1];
                if (a[0] === b[0]) {
                    for (let y = Math.min(a[1], b[1]); y <= Math.max(a[1], b[1]); y += GRID) {
                        occupied.add(`${a[0]},${y}`);
                    }
                }
                else if (a[1] === b[1]) {
                    for (let x = Math.min(a[0], b[0]); x <= Math.max(a[0], b[0]); x += GRID) {
                        occupied.add(`${x},${a[1]}`);
                    }
                }
            }
        }
        const termCount = new Map();
        for (const p of vparts) {
            for (const t of partTerminals(p)) {
                const k = `${t.pos[0]},${t.pos[1]}`;
                termCount.set(k, (termCount.get(k) || 0) + 1);
            }
        }
        const partsLayer = el('g', {}, svgEl);
        const labelLayer = el('g', {}, svgEl);
        for (const p of vparts) {
            if (!ELEM_TYPES[p.type])
                continue; // unknown kind in foreign data
            const open = new Set();
            for (const t of partTerminals(p)) {
                const k = `${t.pos[0]},${t.pos[1]}`;
                if (!occupied.has(k) && (termCount.get(k) || 0) <= 1)
                    open.add(t.name);
            }
            // hitParent keeps drawPart from touching the editor's `svg`
            // global (labels and hit-rects land in labelLayer instead).
            partsLayer.appendChild(drawPart(p, { hitParent: labelLayer, openTerminals: open }));
        }
        // Layer 3: junction dots where >=3 endpoints meet (same counting
        // rule as the editor's drawJunctions).
        const counts = new Map();
        const bump = ([x, y], n) => {
            const k = `${x},${y}`;
            counts.set(k, (counts.get(k) || 0) + n);
        };
        for (const p of vparts) {
            for (const t of partTerminals(p))
                bump(t.pos, 1);
        }
        for (const w of vwires) {
            if (w.bad || !w.points || w.points.length < 2)
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
            el('circle', { cx: x, cy: y, r: 3.5, class: 'node-dot' }, svgEl);
        }
    }
    function drawViewerNetLabels() {
        const longestSeg = (w) => {
            let best = 0;
            for (let i = 1; i < w.points.length; i++) {
                best = Math.max(best, Math.abs(w.points[i][0] - w.points[i - 1][0]) +
                    Math.abs(w.points[i][1] - w.points[i - 1][1]));
            }
            return best;
        };
        const picked = new Map();
        for (const w of vwires) {
            const lab = (w.label || '').trim();
            if (!lab || !w.points || w.points.length < 2)
                continue;
            const cur = picked.get(lab);
            if (!cur || longestSeg(w) > longestSeg(cur))
                picked.set(lab, w);
        }
        for (const [lab, w] of picked) {
            const a = wireLabelAnchor(w);
            if (!a)
                continue;
            const tx = a.x + (a.axis === 'v' ? 8 : 0);
            const ty = a.y + (a.axis === 'h' ? -8 : 0);
            const text = el('text', {
                x: tx, y: ty,
                class: 'net-label-text',
                'text-anchor': a.axis === 'v' ? 'start' : 'middle',
                'dominant-baseline': a.axis === 'h' ? 'auto' : 'middle',
            }, svgEl);
            text.textContent = lab;
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
                    width: bb.width + 2 * pad, height: bb.height + 2 * pad,
                    rx: 3, ry: 3,
                    class: 'net-label-bg',
                });
                svgEl.insertBefore(rect, text);
            }
        }
    }
    // ---- camera ---------------------------------------------------------
    function contentBounds() {
        let xmin = Infinity, ymin = Infinity, xmax = -Infinity, ymax = -Infinity;
        for (const p of vparts) {
            if (!ELEM_TYPES[p.type])
                continue;
            const [x0, y0, x1, y1] = partBBox(p);
            xmin = Math.min(xmin, x0);
            ymin = Math.min(ymin, y0);
            xmax = Math.max(xmax, x1);
            ymax = Math.max(ymax, y1);
        }
        for (const w of vwires) {
            for (const [x, y] of w.points || []) {
                xmin = Math.min(xmin, x);
                ymin = Math.min(ymin, y);
                xmax = Math.max(xmax, x);
                ymax = Math.max(ymax, y);
            }
        }
        if (xmin === Infinity)
            return null;
        return [xmin, ymin, xmax, ymax];
    }
    function fitViewer() {
        const b = contentBounds();
        const W = wrapEl.clientWidth, H = wrapEl.clientHeight;
        if (!b) {
            vpan = { x: W / 2, y: H / 2 };
            vzoom = 1;
            renderViewer();
            return;
        }
        const [xmin, ymin, xmax, ymax] = b;
        // Extra right padding: part name/value labels hang off the right
        // edge of partBBox and aren't included in it.
        const padL = 30, padR = 70, padT = 30, padB = 30;
        const w = (xmax - xmin) + padL + padR;
        const h = (ymax - ymin) + padT + padB;
        vzoom = Math.max(VZOOM_MIN, Math.min(W / w, H / h, 2));
        vpan.x = (W - (xmax + xmin) * vzoom) / 2 + (padL - padR) / 2 * vzoom;
        vpan.y = (H - (ymax + ymin) * vzoom) / 2;
        renderViewer();
    }
    function setViewerZoom(next, cx, cy) {
        const z = Math.max(VZOOM_MIN, Math.min(VZOOM_MAX, next));
        // Keep the world point under (cx, cy) — default: viewport centre —
        // fixed on screen: screen = world * zoom + pan.
        const ax = cx ?? wrapEl.clientWidth / 2;
        const ay = cy ?? wrapEl.clientHeight / 2;
        const wx = (ax - vpan.x) / vzoom, wy = (ay - vpan.y) / vzoom;
        vzoom = z;
        vpan.x = ax - wx * vzoom;
        vpan.y = ay - wy * vzoom;
        renderViewer();
    }
    // ---- interactions (pan / zoom only — nothing edits) ----------------
    function bindInteractions() {
        let panning = false;
        let last = { x: 0, y: 0 };
        wrapEl.addEventListener('mousedown', (e) => {
            if (e.button !== 0)
                return;
            panning = true;
            userMoved = true;
            last = { x: e.clientX, y: e.clientY };
            wrapEl.classList.add('panning');
            e.preventDefault();
        });
        window.addEventListener('mousemove', (e) => {
            if (!panning)
                return;
            vpan.x += e.clientX - last.x;
            vpan.y += e.clientY - last.y;
            last = { x: e.clientX, y: e.clientY };
            renderViewer();
        });
        window.addEventListener('mouseup', () => {
            panning = false;
            wrapEl.classList.remove('panning');
        });
        wrapEl.addEventListener('wheel', (e) => {
            e.preventDefault();
            userMoved = true;
            const r = wrapEl.getBoundingClientRect();
            setViewerZoom(vzoom * Math.exp(-e.deltaY * 0.0015), e.clientX - r.left, e.clientY - r.top);
        }, { passive: false });
        wrapEl.addEventListener('dblclick', () => {
            userMoved = false;
            fitViewer();
        });
        window.addEventListener('resize', () => {
            if (userMoved)
                renderViewer();
            else
                fitViewer();
        });
        document.getElementById('vz-in').addEventListener('click', () => {
            userMoved = true;
            setViewerZoom(vzoom * VZOOM_STEP);
        });
        document.getElementById('vz-out').addEventListener('click', () => {
            userMoved = true;
            setViewerZoom(vzoom / VZOOM_STEP);
        });
        document.getElementById('vz-fit').addEventListener('click', () => {
            userMoved = false;
            fitViewer();
        });
    }
    // ---- boot -----------------------------------------------------------
    async function initViewer() {
        applyPageOptions();
        const raw = rawCircuitParam();
        if (!raw) {
            showMessage('No circuit data', 'Append a base64 circuit to the URL: viewer.html#data=… — the '
                + 'editor’s “Copy embed code” command builds one for you.');
            controlsEl.hidden = true;
            return;
        }
        const doc = decodeCircuitB64(raw);
        if (!doc) {
            showMessage('Couldn’t read this circuit', 'The #data= payload isn’t valid base64-encoded SEDRA JSON. '
                + 'Re-copy the link from the editor and try again.');
            controlsEl.hidden = true;
            return;
        }
        await loadGlyphs();
        vparts = doc.parts || [];
        vwires = doc.wires || [];
        // "Open in SEDRA" carries the same payload into the full editor.
        openLink.href = `index.html#data=${raw}`;
        fitViewer();
        bindInteractions();
    }
    void initViewer().then(() => {
        // Test/debug handle — the only name this file exposes globally.
        window.sedraViewer = {
            ready: true,
            parts: () => vparts,
            wires: () => vwires,
            zoom: () => vzoom,
            pan: () => ({ ...vpan }),
            fit: fitViewer,
            setZoom: setViewerZoom,
            render: renderViewer,
        };
    });
})();
