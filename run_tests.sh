#!/usr/bin/env bash
# Run the full sycan test suite.
#
# Usage:
#   ./run_tests.sh            # full suite
#   ./run_tests.sh drawing    # just the drawing/autodraw tests
#   ./run_tests.sh -- -k diff_pair  # forward args to pytest
#
# Re-renders any *.svg in tests/drawing/diagrams/ as a PNG (via
# ImageMagick `convert`) when --png is passed.
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -x ".venv/bin/pytest" ]; then
    echo "error: .venv/bin/pytest not found." >&2
    echo "       create the venv with 'uv venv' (or python -m venv .venv)" >&2
    echo "       and install pytest into it before running this script." >&2
    exit 1
fi

PYTEST=.venv/bin/pytest
ARGS=("-q")
RENDER_PNG=0
TARGETS=()

while [ "$#" -gt 0 ]; do
    case "$1" in
        --png) RENDER_PNG=1 ;;
        --) shift; ARGS+=("$@"); break ;;
        drawing|tests/drawing) TARGETS+=("tests/drawing") ;;
        *) ARGS+=("$1") ;;
    esac
    shift
done

if [ "${#TARGETS[@]}" -eq 0 ]; then
    TARGETS=("tests")
fi

echo "==> running pytest ${ARGS[*]} ${TARGETS[*]}"
"$PYTEST" "${ARGS[@]}" "${TARGETS[@]}"

if [ "$RENDER_PNG" -eq 1 ]; then
    if ! command -v convert >/dev/null 2>&1; then
        echo "warning: 'convert' (ImageMagick) not found; skipping PNG render." >&2
    else
        echo "==> rendering tests/drawing/diagrams/*.svg → *.png"
        for svg in tests/drawing/diagrams/*.svg; do
            [ -f "$svg" ] || continue
            convert -background white -density 150 "$svg" "${svg%.svg}.png" 2>/dev/null
        done
    fi
fi
