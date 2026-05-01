#!/usr/bin/env bash
# End-to-end backend benchmark driver.
#
# Runs bench/bench_backends.py once per backend and writes the combined
# JSON to bench/results.json (overwriting). Also measures cold-import
# time externally for each backend (a single ``python -c "import sycan"``
# in a fresh subprocess, repeated N_IMPORT times for a median).
set -euo pipefail
cd "$(dirname "$0")/.."

OUT="bench/results.json"
N_IMPORT=5

PY=.venv/bin/python

import_median_ms() {
    local backend="$1"
    local times=()
    for _ in $(seq "$N_IMPORT"); do
        local t
        t=$(SYCAN_CAS_BACKEND="$backend" "$PY" -c "
import time, os
os.environ['SYCAN_CAS_BACKEND']='$backend'
t0 = time.perf_counter()
import sycan
print(round((time.perf_counter()-t0)*1000, 3))
")
        times+=("$t")
    done
    printf '%s\n' "${times[@]}" | sort -n | awk 'NR==int(NF/2)+1 || NR==int(('"$N_IMPORT"'+1)/2){print; exit}'
}

echo "{" > "$OUT"
first=1
for backend in sympy symengine; do
    echo "==> $backend"
    imp=$(import_median_ms "$backend")
    echo "    cold import median: ${imp} ms"

    json=$(SYCAN_CAS_BACKEND="$backend" "$PY" bench/bench_backends.py)

    if [ $first -eq 1 ]; then first=0; else echo "," >> "$OUT"; fi
    echo "  \"$backend\": {" >> "$OUT"
    echo "    \"cold_import_ms\": $imp," >> "$OUT"
    echo "    \"bench\":" >> "$OUT"
    echo "$json" | sed 's/^/      /' >> "$OUT"
    printf '  }' >> "$OUT"
done
echo "" >> "$OUT"
echo "}" >> "$OUT"

echo
echo "==> wrote $OUT"
