#!/usr/bin/env bash
# Convert Inkscape-edited SVGs in res/inkscape/ to plain SVGs in res/.
#
# Plain SVGs drop inkscape:* / sodipodi:* namespaces, attributes, and
# <sodipodi:namedview>, which improves portability across renderers.
#
# Usage:
#   ./svg_to_plain.sh                # all *.svg in res/inkscape/
#   ./svg_to_plain.sh foo.svg ...    # only the named source files
#   INKSCAPE=/path/to/inkscape ./svg_to_plain.sh
set -euo pipefail

INKSCAPE="${INKSCAPE:-$HOME/Applications/Inkscape-0d15f75-x86_64_ac4b7147325d3a7d2666677069ab8722.AppImage}"
if [[ ! -x "$INKSCAPE" ]] && ! command -v "$INKSCAPE" >/dev/null 2>&1; then
    if command -v inkscape >/dev/null 2>&1; then
        INKSCAPE=inkscape
    else
        echo "inkscape not found; set INKSCAPE=/path/to/inkscape" >&2
        exit 1
    fi
fi

res_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
src_dir="$res_dir/inkscape"

if [[ ! -d "$src_dir" ]]; then
    echo "source directory not found: $src_dir" >&2
    exit 1
fi

if [[ $# -gt 0 ]]; then
    sources=()
    for arg in "$@"; do
        if [[ -f "$src_dir/$arg" ]]; then
            sources+=("$src_dir/$arg")
        elif [[ -f "$arg" ]]; then
            sources+=("$arg")
        else
            echo "not found: $arg" >&2
            exit 1
        fi
    done
else
    shopt -s nullglob
    sources=("$src_dir"/*.svg)
fi

if [[ ${#sources[@]} -eq 0 ]]; then
    echo "no SVG files found in $src_dir" >&2
    exit 1
fi

for src in "${sources[@]}"; do
    name="$(basename "$src")"
    dest="$res_dir/$name"
    "$INKSCAPE" --export-plain-svg --export-overwrite \
        --export-filename="$dest" "$src" >/dev/null 2>&1
    echo "  $name"
done

echo "converted ${#sources[@]} file(s) to $res_dir/"
