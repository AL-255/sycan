# SYCAN

![SYCAN Logo](https://github.com/AL-255/sycan/raw/main/res/sycan.png)

[![CI](https://github.com/AL-255/sycan/actions/workflows/ci.yml/badge.svg)](https://github.com/AL-255/sycan/actions/workflows/ci.yml) [![Pages](https://github.com/AL-255/sycan/actions/workflows/pages/pages-build-deployment/badge.svg)](https://al-255.github.io/sycan/)

**SYCAN** (SYmbolic Circuit ANalysis) is a Python package for symbolic
circuit simulation built on SymPy. It provides closed-form DC, AC, noise,
transfer-function, pole–zero, and sensitivity analyses, plus automatic
schematic drawing and an in-browser REPL.

## Install

Requires Python ≥ 3.11.

```console
pip install sycan
```

Or with [uv](https://docs.astral.sh/uv/):

```console
uv add sycan
```

For a faster CAS backend (optional):

```console
pip install "sycan[symengine]"
```

Then opt in by setting `SYCAN_CAS_BACKEND=symengine` before importing
`sycan`. The symengine backend covers ~95 % of the test suite (10 tests
skipped on representation / API divergences) and is **7–8× faster** than
SymPy on AC and noise solves and ~30 % faster on headroom analysis — see
[`docs/BE_BENCHMARK.md`](docs/BE_BENCHMARK.md) and
[`docs/BE_PORT_STATUS.md`](docs/BE_PORT_STATUS.md).

## Quick start

```python
from sycan import cas, Circuit, solve_ac
from sycan.components.basic import Resistor, Capacitor, VoltageSource

R, C = cas.symbols("R C", positive=True)
Vin  = cas.Symbol("Vin")

c = Circuit("RC low-pass")
c.add(VoltageSource("V1", "in", "0", value=0, ac_value=Vin))
c.add(Resistor("R1", "in", "out", R))
c.add(Capacitor("C1", "out", "0", C))

sol = solve_ac(c)
H = sol[cas.Symbol("V(out)")] / Vin
print(cas.simplify(H))   # 1 / (C*R*s + 1)
```

## Try it without installing

The [live REPL](https://al-255.github.io/sycan/repl/) runs SYCAN entirely
in the browser via Pyodide — no install needed. The page ships preset
examples for filters, amplifiers, voltage references, and S-parameter
transmission lines.

## Documentation

Full docs (API reference, tutorial, autodraw pipeline) live at
<https://al-255.github.io/sycan/>.

## Analysis modes

| Solver | Purpose |
| --- | --- |
| `solve_dc` / `solve_dc_sweep` | DC operating point, parameter sweeps |
| `solve_ac` | Small-signal frequency response |
| `solve_tf` | Transfer functions between arbitrary nodes |
| `solve_impedance` | Port impedance with auto termination |
| `solve_noise` | Output-referred noise PSD with per-source breakdown |
| `solve_pz` | Pole–zero extraction |
| `solve_sensitivity` | Component-level sensitivity analysis |
| `solve_headroom` | DC headroom analysis |
| `autodraw` | Automatic schematic SVG rendering |

## Development

```console
git clone https://github.com/AL-255/sycan
cd sycan
uv sync --dev
./run_tests.sh
```

## License

GPL v2 — see [`LICENSE`](LICENSE).

## Credits

The project heavily references the work of [ahkab](https://github.com/ahkab/ahkab) and [ngSpice](https://ngspice.sourceforge.io/). Kudos to them for their contributions.
