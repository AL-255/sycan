# Version Bump Notes

When bumping `version` in `pyproject.toml`, the wheel filename
(`sycan-X.Y.Z-py3-none-any.whl`) is referenced from a few places that
load it via Pyodide. `run_webpage.sh` rewrites them on build, but the
**source-tree references must also stay current** so checkouts work
without running the build script.

## Files to update

- `docs/repl/index.html` — REPL `micropip.install(...)` URL.
- `docs/sedra/src/editor.ts` — Sedra editor's `ensureSycan()` call.
- `docs/sedra/editor.js` — compiled artifact; re-run `tsc` (or just
  rerun `run_webpage.sh --sedra` and let it sed-replace).

A single sed pass covers all three:

```bash
NEW=0.1.X   # the new version
sed -i '' -E "s|sycan-[0-9]+\.[0-9]+\.[0-9]+-py3-none-any\.whl|sycan-${NEW}-py3-none-any.whl|g" \
    docs/repl/index.html \
    docs/sedra/src/editor.ts \
    docs/sedra/editor.js
```

## When adding a new wheel-loading site

Add the new file path to:

1. The sed list in `run_webpage.sh` (both the `--sedra` block and the
   full-deploy block).
2. The "Files to update" list above.
