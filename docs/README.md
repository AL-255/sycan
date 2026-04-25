# docs/

- `index.html` — in-browser sycan REPL (Pyodide + CodeMirror + MathJax),
  live at <https://al-255.github.io/sycan/>.
- `examples/` — preset example scripts loaded by the REPL on demand.
  Python examples that evaluate to an SVG string render inline in the
  page's schematic preview; `examples/autodraw.py` demonstrates this.
  - `manifest.json` — list of `{label, file}` entries; the page builds one
    button per entry. To add an example: drop a `.py` file in this folder
    and append a line to `manifest.json`.
  - `*.py` — one example per file, edited as ordinary Python (no JS-string
    escaping pitfalls).
- `sycan-*.whl` — the wheel `index.html` installs via `micropip`.

## Preview locally

`file://` won't work (Pyodide needs HTTP). Serve the folder:

```bash
python -m http.server --directory docs 8000
# open http://localhost:8000/
```

## After editing sycan code

```bash
uv build
cp dist/sycan-*.whl docs/
```

Then hard-reload the page (Ctrl/⌘-Shift-R). If the browser still serves the
stale wheel, bump `version` in `pyproject.toml`, rebuild, and update the
filename in `micropip.install('./sycan-<new>-py3-none-any.whl')`.
