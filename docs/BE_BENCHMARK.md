# CAS backend benchmark

End-to-end timing comparison between the two `sycan.cas` backends —
sympy (default) and symengine — across a fixed set of representative
sycan workflows.

## Methodology

Driver: [`bench/run.sh`](../bench/run.sh) calls
[`bench/bench_backends.py`](../bench/bench_backends.py) once per
backend (`SYCAN_CAS_BACKEND={sympy,symengine}`) and writes
`bench/results.json`.

For each scenario the harness runs the body **7 times** and reports:

- **Cold** — wall time of the very first call (after a single warmup
  inside the harness's setup function), so JIT / cache effects show up.
- **Warm median** — median of the remaining 6 calls.

Cold import is measured externally (median of 5 fresh
`python -c "import sycan"` runs), since module-level work cannot be
captured from inside the process.

## Environment

- CPU: AMD Ryzen 9 9955HX (16 cores)
- OS: Ubuntu 24.04.3 LTS, Linux 6.17
- Python 3.13.9
- sympy 1.14.0, symengine 0.14.1, numpy 2.4.4
- sycan @ HEAD of `main`

## Results

Times are milliseconds. **Ratio** = symengine / sympy; values < 1 mean
the symengine backend is faster.

### Warm (median of 6 hot calls)

| Scenario                          |  sympy ms |  SE ms | ratio | note                                   |
|-----------------------------------|----------:|-------:|------:|----------------------------------------|
| parse_value (6 tokens)            |   0.011   |  0.015 | 1.35× | tiny; per-token bookkeeping            |
| parse small netlist               |   0.011   |  0.009 | 0.85× | tiny                                   |
| **solve_noise RC thermal**        |   0.374   |  0.050 | **0.13×** | native LU per noise source         |
| **solve_ac H(s) RC lowpass**      |   0.328   |  0.044 | **0.14×** | native LU, no bridge               |
| **solve_impedance Z_in / Z_out**  |   1.131   |  0.161 | **0.14×** | native LU + Matrix ops             |
| build_mna DC (2T-vref)            |   0.083   |  0.055 | 0.67× | native stamping                        |
| butterworth(5) prototype          |   0.685   |  0.558 | 0.81× | sp.Poly bridged → sympy                |
| **solve_headroom (op_point)**     |  24.85    | 16.98  | **0.68×** | native C++ solve per predicate     |
| lambdify+evaluate 32×32 grid      |   0.504   |  0.536 | 1.06× | numpy dominates                        |
| solve_dc nonlinear (CS amp)       |   7.19    |  7.49  | 1.04× | system solve, bridged to sympy         |
| solve_dc linear (vdivider)        |   5.07    |  5.40  | 1.06× | simplify=True bridges to sympy         |

### Cold (first call, in-process)

| Scenario                          |  sympy ms |   SE ms | ratio |
|-----------------------------------|----------:|--------:|------:|
| solve_ac H(s) RC lowpass          |   2.79    |   0.09  | 0.03× |
| solve_impedance Z_in / Z_out      |   6.77    |   0.26  | 0.04× |
| solve_noise RC thermal            |   1.54    |   0.10  | 0.07× |
| parse_value (6 tokens)            |   0.20    |   0.07  | 0.32× |
| butterworth(5) prototype          |   8.72    |   3.11  | 0.36× |
| build_mna DC (2T-vref)            |   0.15    |   0.10  | 0.63× |
| solve_headroom (op_point)         |  49.4     |  40.7   | 0.82× |
| solve_dc nonlinear (CS amp)       |  16.8     |  16.9   | 1.00× |
| lambdify+evaluate 32×32 grid      |  41.9     |  41.6   | 0.99× |
| solve_dc linear (vdivider)        |  59.9     |  59.6   | 0.99× |
| parse small netlist               |   0.04    |   0.04  | 0.85× |

### Cold import (median of 5 fresh subprocesses)

| backend   | ms    |
|-----------|------:|
| sympy     | 118.4 |
| symengine | 202.3 |

The symengine backend is slower to import because both sympy *and* symengine
have to be loaded — the bridge wrappers (`simplify`, `solve`, `Poly`, …)
keep sympy in the picture.

## Reading the numbers

**Where symengine wins (≈7–8× warm, 16–32× cold).** Anything that runs
through native LU plus matrix algebra and never touches the sympy
bridges: `solve_ac`, `solve_impedance`, `solve_noise`, `build_mna`.
These workflows touch only names symengine ships natively
(`Matrix.LUsolve`, indexing, `subs`, `diff`), so the entire pipeline
runs in C++.

**Where the two are roughly tied.** Anything dominated by a sympy
operation symengine cannot do natively — `Poly` and system /
`dict=True` solves. The wrapper sympifies the input, runs sympy, and
converts back, so the underlying CAS doing the heavy lifting is the
same in both cases. Examples: `solve_dc` with `simplify=True`
(repeated `Basic.simplify()` on each LU output entry — fast itself,
but solve_dc is bound by the LU and stamping cost), `solve_dc` with a
nonlinear residual (system), `butterworth` (uses `Poly`). The
roundtrip overhead is small (~5–10%).

**Headroom is a partial-native case.** `solve_headroom` calls
`sp.solve(eq, var)` once per saturation-predicate boundary —
single-equation polynomial solves that the C++ symengine `solve`
handles natively — and `sp.simplify` on each predicate, which goes
to `Basic.simplify()` natively as well. Net warm speedup ≈32%.

**Native simplify path.** `simplify`, `cancel`, `together`,
`fraction`, and `trigsimp` all route to symengine's
`Basic.simplify()` / `Basic.as_numer_denom()` rather than sympy. The
warm-path numbers don't move much in this table (the workloads
shown spend most of their time in LU and stamping, not simplify),
but the implementation change unblocked two tests that previously
diverged because the sympy round-trip returned a different canonical
form than the native call (see
[BE_PORT_STATUS.md](BE_PORT_STATUS.md), skip list went from 12 → 10).

**Where symengine is slightly slower.** Workloads dominated by code
*outside* the CAS — numpy evaluation in `lambdify`, parsing tokens —
where symengine's higher per-call construction cost is a few µs per
operation and there is no large symbolic computation to amortise it.

**Cold-call asymmetry.** Sympy spends multiple milliseconds the first
time it touches a code path because of lazy submodule imports
(`sympy.matrices.dense`, `sympy.simplify.simplify`, …). Symengine has
already done all of that in C++ at import time, so the first AC /
impedance / noise call is essentially as fast as the warm one.
That is why the cold-call ratios for those scenarios are 30× rather
than 8×.

## When to pick which backend

- **sympy** — default; pick it when you care about canonical
  expression form (e.g. comparing to a hand-derived closed form), when
  you depend on sympy-only APIs (`.rewrite`, full `.has(class)`,
  assumption-aware `is_real`/`is_positive`), or when your workload is
  dominated by `simplify`/`solve` (you pay the same sympy cost either
  way).
- **symengine** — pick it when your hot path is symbolic LU /
  small-signal AC sweeps / impedance / noise PSDs and you don't need
  pristine simplification of the result. The 7–8× warm speedup
  compounds quickly across thousands of frequency-point evaluations
  or Monte-Carlo runs. Be aware of the representation caveats
  documented in [BE_PORT_STATUS.md](BE_PORT_STATUS.md) — some sycan
  tests rely on sympy's auto-canonical forms and are skipped under
  the SE backend.

## Reproducing

```bash
.venv/bin/pip install symengine    # if not already present
bench/run.sh                       # writes bench/results.json
```

`bench/results.json` is overwritten in place; commit it alongside this
report when you re-benchmark.
