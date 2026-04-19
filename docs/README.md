# docs/

- `index.html` — in-browser sycan REPL (Pyodide), live at <https://al-255.github.io/sycan/>.
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
