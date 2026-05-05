#!/usr/bin/env bash
# Build and serve the sycan GitHub Pages site locally.
#
# Mirrors what the `pages` job in .github/workflows/ci.yml does:
#   1. uv build           -> fresh wheel for the REPL
#   2. assemble _site/    -> docs/* overlaid + freshly built wheel
#   3. sphinx-build       -> Sphinx HTML on top of _site/
#   4. python http.server -> serve _site/ on http://localhost:$PORT/
#
# Usage:
#   ./run_webpage.sh              # build + serve on :8000
#   ./run_webpage.sh 8080         # build + serve on :8080
#   ./run_webpage.sh --no-build   # skip rebuild, just serve existing _site/
#   ./run_webpage.sh --sedra      # serve docs/ only (editor at /sedra/, no
#                                 # uv build, no Sphinx build) — much faster
#                                 # iteration loop when only touching the
#                                 # browser editor. Runs `tsc` first if any
#                                 # docs/sedra/src/*.ts is newer than the
#                                 # emitted JS.
set -euo pipefail

cd "$(dirname "$0")"

# Portable in-place sed.
#
# GNU sed (Linux) takes `-i` with no argument; BSD sed (macOS) takes
# `-i <ext>` and creates `<file><ext>` as a backup. The form
# `-i.bak` (extension attached to the flag, no space) is accepted by
# both, so we use that and clean up the .bak files afterwards. The
# `-E` flag for extended regex is also portable across modern GNU
# and BSD sed.
sed_inplace() {
    local script="$1"; shift
    sed -i.bak -E "$script" "$@"
    for f in "$@"; do
        rm -f "${f}.bak"
    done
}

PORT=8000
REBUILD=1
SEDRA_ONLY=0
for arg in "$@"; do
    case "$arg" in
        --no-build) REBUILD=0 ;;
        --sedra) SEDRA_ONLY=1 ;;
        --help|-h)
            sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            if [[ "$arg" =~ ^[0-9]+$ ]]; then
                PORT="$arg"
            else
                echo "error: unrecognised argument '$arg'" >&2
                exit 2
            fi
            ;;
    esac
done

# Fast path: skip every uv/sphinx step and just serve the static
# `docs/` tree. The editor lives at `/sedra/`; the REPL works too once
# the wheel under docs/repl/ is in sync with the current pyproject
# version (handled below).
#
# SEDRA's TypeScript sources live at docs/sedra/src/{glyphs,editor}.ts
# and compile to docs/sedra/{glyphs,editor}.js (the same paths
# index.html loads as classic <script defer> tags). We rebuild the
# JS first if the sources are newer than the emitted artifacts, then
# serve. Skip silently if `npx` isn't installed — index.html falls
# back to whatever JS is already on disk.
if [ "$SEDRA_ONLY" -eq 1 ]; then
    if [ "$REBUILD" -eq 1 ] && command -v npx >/dev/null 2>&1 \
       && [ -f docs/sedra/tsconfig.json ]; then
        latest_src=$(find docs/sedra/src -name '*.ts' -newer docs/sedra/editor.js 2>/dev/null | head -n 1 || true)
        if [ ! -f docs/sedra/editor.js ] || [ -n "$latest_src" ]; then
            echo "==> tsc docs/sedra/src/*.ts -> docs/sedra/*.js"
            (cd docs/sedra && npx --no-install tsc) || {
                echo "    (tsc failed; serving stale docs/sedra/*.js)" >&2
            }
        fi
    fi

    # Refresh the REPL wheel under docs/repl/ so it matches the current
    # pyproject version. Without this, the REPL's micropip.install()
    # call points at a stale (or missing) wheel filename whenever the
    # version bumps. uv build is cached, so re-running on an unchanged
    # source tree is fast. Skip silently if uv isn't installed — the
    # editor still works on its own.
    if [ "$REBUILD" -eq 1 ] && command -v uv >/dev/null 2>&1; then
        version=$(awk -F'"' '/^version *=/{print $2; exit}' pyproject.toml)
        target_wheel="sycan-${version}-py3-none-any.whl"
        if [ ! -f "docs/repl/${target_wheel}" ]; then
            echo "==> uv build (refreshing docs/repl/${target_wheel})"
            uv build
            rm -f docs/repl/sycan-*-py3-none-any.whl
            cp "dist/${target_wheel}" docs/repl/
            sed_inplace \
                "s|sycan-[0-9]+\.[0-9]+\.[0-9]+-py3-none-any\.whl|${target_wheel}|g" \
                docs/repl/index.html \
                docs/sedra/src/editor.ts \
                docs/sedra/editor.js
        fi
    fi

    echo "==> serving docs/ on http://localhost:${PORT}/"
    echo "    Editor: http://localhost:${PORT}/sedra/"
    echo "    REPL:   http://localhost:${PORT}/repl/"
    echo "    (--sedra: no Sphinx build; tsc and uv build run only when stale)"
    echo "    (Ctrl-C to stop)"
    exec python -m http.server --directory docs "$PORT"
fi

if ! command -v uv >/dev/null 2>&1; then
    echo "error: 'uv' not found on PATH; install from https://astral.sh/uv" >&2
    exit 1
fi

if [ "$REBUILD" -eq 1 ]; then
    echo "==> uv sync --group docs"
    uv sync --group docs

    echo "==> uv build (fresh wheel for the REPL)"
    uv build

    echo "==> assembling _site/ from docs/"
    rm -rf _site
    mkdir _site
    cp -r docs/. _site/
    # SEDRA's TypeScript build artefacts and dependencies don't ship to
    # the deployed site — only the compiled .js does.
    rm -rf _site/sedra/node_modules \
           _site/sedra/src \
           _site/sedra/package.json \
           _site/sedra/package-lock.json \
           _site/sedra/tsconfig.json \
           _site/sedra/tsconfig.tsbuildinfo \
           _site/sedra/.gitignore

    # Compile TypeScript first if the sources have moved ahead of the
    # committed JS — keeps the deployed editor in lockstep with the
    # source tree without forcing every contributor to remember the
    # build step.
    if command -v npx >/dev/null 2>&1 && [ -f docs/sedra/tsconfig.json ]; then
        latest_src=$(find docs/sedra/src -name '*.ts' -newer docs/sedra/editor.js 2>/dev/null | head -n 1 || true)
        if [ ! -f docs/sedra/editor.js ] || [ -n "$latest_src" ]; then
            echo "==> tsc docs/sedra/src/*.ts -> docs/sedra/*.js (refreshed)"
            (cd docs/sedra && npx --no-install tsc) || {
                echo "    (tsc failed; deploying stale docs/sedra/*.js)" >&2
            }
            # Re-copy the regenerated JS into _site.
            cp docs/sedra/glyphs.js docs/sedra/editor.js _site/sedra/
        fi
    fi

    rm -f _site/repl/sycan-*-py3-none-any.whl
    cp dist/sycan-*-py3-none-any.whl _site/repl/
    wheel=$(basename _site/repl/sycan-*-py3-none-any.whl)
    sed_inplace \
        "s|sycan-[0-9]+\.[0-9]+\.[0-9]+-py3-none-any\.whl|${wheel}|g" \
        _site/repl/index.html \
        _site/sedra/editor.js
    echo "    -> $wheel"

    echo "==> sphinx-build sphinx -> _site/"
    uv run sphinx-build -b html --keep-going sphinx _site
    touch _site/.nojekyll
else
    if [ ! -d _site ]; then
        echo "error: _site/ does not exist; rerun without --no-build" >&2
        exit 1
    fi
fi

echo
echo "==> serving _site/ on http://localhost:${PORT}/"
echo "    REPL: http://localhost:${PORT}/repl/"
echo "    (Ctrl-C to stop)"
exec python -m http.server --directory _site "$PORT"
