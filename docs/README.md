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
  <https://github.com/andrescg2sj/Sycan>; now a TypeScript app with:
  snap-grid canvas, full SYCAN component library (R, L, C, V, I, D,
  NPN/PNP BJT, NMOS/PMOS — 3T and 4T, triode, VCVS/VCCS/CCCS/CCVS,
  ground, wire), click-to-place + Manhattan multi-segment wires with
  Steiner T-junction coalescing, A\* auto-router for drag-mode wire
  re-routing with parity check, segment-level selection, undo/redo,
  copy/paste, auto-naming, drag-to-move with optional bad-connection
  placeholders, net highlight overlay, symbolic node-voltage solver
  (`Calc Node` — runs sycan in the page via Pyodide), netlist export,
  JSON save/load, notification center.
  - `index.html` — markup + inline `<style>`.
  - `src/glyphs.ts`, `src/editor.ts` — TypeScript sources.
  - `glyphs.js`, `editor.js` — `tsc` output, loaded as classic
    `<script defer>` tags in document order so `glyphs.ts` symbols
    (`ELEM_TYPES`, `drawPart`, the cross-file `interface`s, …) are
    in scope by the time `editor.js` runs. `tsconfig.json` sets
    `module: "none"` to keep the two files sharing one global scope,
    so neither file uses `import`/`export`.
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
