# docs/

Source material for the GitHub Pages site at
<https://al-255.github.io/sycan/>. The deployed site is assembled in CI:
the Sphinx API docs (built from `../sphinx/`) become the root, and this
folder is overlaid into `_site/` so the REPL ends up at `/repl/`.

- `repl/` — in-browser sycan REPL (Pyodide + CodeMirror + MathJax).
  - `index.html` — the REPL page itself, served at `/repl/`.
  - `examples/` — preset example scripts loaded on demand.
    - `manifest.json` — `{category, label, file}` entries grouped
      into one `<optgroup>` per category in the example picker.
    - `*.py` — one example per file.
  - `res/` — glyph SVGs (mirrored from top-level `res/`) plus the
    SYCAN logo.
  - `sycan-*.whl` — the wheel the page installs via `micropip`.
- `sedra/` — SYCAN's in-browser schematic capture editor, served at
  `/sedra/`. Originally inspired by the Java circedit reference in
  <https://github.com/andrescg2sj/Sycan>; now a TypeScript app built
  to a power-user productivity bar:
  - **Editing** — full SYCAN component library (R/L/C, V/I sources,
    diode, NPN/PNP, NMOS/PMOS 3T/4T, triode, VCVS/VCCS/CCCS/CCVS,
    ground), Manhattan wires with Steiner T-junction coalescing,
    KiCad-style drag (attached wires stretch at their moving end,
    connectivity preserved by construction, optional parity guard),
    KiCad marquee semantics (window vs crossing select), flip/rotate,
    inline value editing (dbl-click/F2), duplicate, arrow-key nudge,
    select-similar, Tab cycling, renumber, undoable Clear.
  - **Command surfaces** — one command registry drives the right-click
    context menu, the Ctrl+K fuzzy command palette, and the `?`
    shortcut cheat sheet; gear popover for drag options; More popover
    for long-tail parts.
  - **Feedback** — proactive ERC overlay (floating pins, dangles,
    duplicate refs, missing ground) with click-to-locate badges,
    zoned status bar (mode/hint/selection/grid/coords/zoom/ERC),
    wire-snap rings, hover pre-selection, flash halos on paste/undo,
    multi-net highlight pinning with legend chips, starter card.
  - **Analysis & I/O** — symbolic node-voltage solver (`Calc Node`,
    sycan via Pyodide with staged loading progress), MNA matrix
    viewer, netlist export, standalone SVG export + PNG clipboard
    copy, JSON save/load.
  - **Embedding** — `viewer.html` is a standalone view-only widget:
    it renders a base64-encoded circuit document carried in the URL
    fragment (`viewer.html#data=<b64>`, URL-safe alphabet, padding
    optional) with fit-to-content, drag-pan, wheel-zoom, an
    Open-in-SEDRA link, and `?theme=light|dark` / `?controls=0`
    options. The editor's Ctrl+K commands *Copy embed code
    (view-only iframe)* and *Copy view-only link* produce the
    snippet/URL; opening the editor itself with `#data=<b64>`
    imports the linked schematic (confirming before replacing a
    non-empty one). The codec lives in `glyphs.ts`
    (`encodeCircuitB64`/`decodeCircuitB64`) — the one file both
    pages share.

    ```html
    <iframe src="https://…/sedra/viewer.html#data=<base64>"
            width="720" height="480" loading="lazy"
            title="SEDRA schematic (view-only)"></iframe>
    ```

    `embed-example.html` is a runnable copy of this — a CSS-free
    plain-HTML page with a voltage divider baked into the fragment
    (served at `/sedra/embed-example.html`).
  - **Design system** — token-driven dark/light themes (surface
    elevation scale, semantic colors, JetBrains Mono), uniform 20×20
    icon grammar, zoom-adaptive grid, collapsible panels.
  - `index.html` — markup + inline `<style>`. `viewer.html` — the
    embeddable view-only page (loads `glyphs.js` + `viewer.js` only).
  - `src/glyphs.ts`, `src/editor.ts`, `src/viewer.ts` — TypeScript
    sources.
  - `glyphs.js`, `editor.js`, `viewer.js` — `tsc` output, loaded as
    classic `<script defer>` tags in document order so `glyphs.ts`
    symbols (`ELEM_TYPES`, `drawPart`, the cross-file `interface`s, …)
    are in scope by the time `editor.js` / `viewer.js` runs.
    `tsconfig.json` sets `module: "none"` to keep the files sharing
    one global scope, so none of them uses `import`/`export`
    (`viewer.ts` wraps everything in an IIFE to avoid top-level
    collisions with `editor.ts`).
  - `tests/` — Puppeteer-driven browser test suite. `node tests/run.mjs`
    (or `npm test`) compiles TypeScript if needed, spawns a local
    `python -m http.server`, then runs every `*.test.mjs` against the
    live page.
  - `package.json`, `tsconfig.json` — only used during local builds /
    tests. Stripped from `_site/` by `run_webpage.sh` before deploy.

  Iterate quickly with `./run_webpage.sh --sedra` (no Sphinx, no
  wheel build — runs `tsc` first if any `src/*.ts` is newer than
  the emitted JS, then serves `docs/` over plain HTTP).
- `analysis.md`, `level_shifter.py`, `tline_sparams.py` — standalone
  reference material kept alongside the site.
- `BE_BENCHMARK.md`, `BE_PORT_STATUS.md` — sympy↔symengine backend
  benchmark report and migration status.
- `ROUTER_BENCHMARK.md` — autodraw final-routing-pass comparison
  between the default Dijkstra and the optional A\* router (selected
  via ``autodraw(router="astar")``); includes the harness in
  `bench/bench_router.py`.

## Preview locally

`file://` won't work (Pyodide needs HTTP). Serve the REPL folder:

```bash
python -m http.server --directory docs/repl 8000
# open http://localhost:8000/
```

To preview the full deployed site (Sphinx + REPL) locally:

```bash
uv sync --group docs
rm -rf _site && mkdir _site && cp -r docs/. _site/
uv run sphinx-build -b html sphinx _site
python -m http.server --directory _site 8000
```

## After editing sycan code

```bash
uv build
cp dist/sycan-*.whl docs/repl/
```

Then hard-reload the page (Ctrl/⌘-Shift-R). If the browser still serves
the stale wheel, bump `version` in `pyproject.toml`, rebuild, and update
the filename in `micropip.install('./sycan-<new>-py3-none-any.whl')`.
