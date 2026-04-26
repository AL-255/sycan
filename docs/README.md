# docs/

Source material for the GitHub Pages site at
<https://al-255.github.io/sycan/>. The deployed site is assembled in CI:
the Sphinx API docs (built from `../sphinx/`) become the root, and this
folder is overlaid into `_site/` so the REPL ends up at `/repl/`.

- `repl/` — in-browser sycan REPL (Pyodide + CodeMirror + MathJax).
  - `index.html` — the REPL page itself, served at `/repl/`.
  - `examples/` — preset example scripts loaded on demand.
    - `manifest.json` — `{label, file}` entries; one button per entry.
    - `*.py` — one example per file.
  - `res/` — glyph SVGs (mirrored from top-level `res/`) plus the
    SYCAN logo.
  - `sycan-*.whl` — the wheel the page installs via `micropip`.
- `analysis.md`, `level_shifter.py`, `tline_sparams.py` — standalone
  reference material kept alongside the site.

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
