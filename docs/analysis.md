# SYCAN analysis methods

This is a short reference for every analysis methods _SYCAN_ supports. All
solvers operate on a `Circuit` populated with `Component` instances and
return sympy expressions, so results stay symbolic until you
`subs(...)` or `.evalf()` them.

The MNA system the solvers assemble has the form `A · x = b`, where `x`
is the vector of unknown node voltages followed by auxiliary branch
currents (one per voltage source, current-controlled element, or any
component that opted in via `has_aux = True`).

## DC operating point — `solve_dc(circuit, simplify=True)`

Steady-state node voltages and source branch currents.

- **Capacitors** are treated as opens, **inductors** as shorts (a 0 V
  source enforces `V(n+) = V(n-)` via an auxiliary current).
- **Linear circuits** are solved by symbolic LU on `A · x = b`.
- **Nonlinear circuits** — any component with `has_nonlinear = True`,
  e.g. a diode, BJT, MOSFET, or triode — fall back to `sympy.solve` on
  the residual vector `A · x − b + Σ stamp_nonlinear`. A
  `RuntimeError` is raised if sympy fails to close the system; pin
  more nodes with explicit voltage sources or substitute numeric
  parameters when that happens.

Returns `dict[Symbol, Expr]` mapping `V(node_name)` and
`I(component_name)` symbols to their solved expressions. With
`simplify=True` (the default) every result is run through
`sp.simplify`; pass `False` if simplification is too slow on a large
nonlinear netlist.

```python
sol = solve_dc(circuit)
v_out = sol[sp.Symbol("V(out)")]
```

## Small-signal AC — `solve_ac(circuit, s=None, simplify=False)`

Frequency-domain response in the Laplace variable `s`. Capacitors stamp
admittance `s · C`, inductors `1 / (s · L)`. Independent sources use
their `ac_value` if set; otherwise the DC `value` is reused.

Pass your own `s` (e.g. `sp.Symbol("s")` shared with another
expression, or `sp.I * omega` for a Bode evaluation) — a fresh symbol
is created if you don't.

Nonlinear devices contribute their explicit AC small-signal stamp
(e.g. MOSFET `g_m`, `g_ds`, parasitic caps, evaluated at `V_GS_op`,
`V_DS_op`); devices with no AC stamp are treated as zero-current
elements.

Returns `dict[Symbol, Expr]` keyed the same way as `solve_dc`. Disable
`simplify` (the default) for speed on large symbolic networks.

```python
H = solve_ac(circuit)[sp.Symbol("V(out)")] / Vin
```

## Port impedance — `solve_impedance(circuit, port_name, termination="auto", s=None, simplify=False)`

Small-signal impedance looking into a named `Port`. A 1 V AC test
source is applied across the chosen port and `Z = 1 / (-I_test)` is
read back from its branch current.

The other ports in the netlist are terminated automatically:

- `"z"` — all other ports left open (Z-parameter convention).
- `"y"` — all other ports shorted (Y-parameter convention).
- `"auto"` *(default)* — input ports shorted, output / generic ports
  left open. This is the "sources zeroed, loads open" convention you
  want for amplifier input / output impedance.

The original circuit is left untouched; `solve_impedance` builds a
throw-away copy with the test source and termination wires.

```python
Z_in  = solve_impedance(c, "P_in")           # input impedance
Z_out = solve_impedance(c, "P_out")          # output impedance
```

## Noise PSD — `solve_noise(circuit, output_node, s=None, simplify=False)`

Output-voltage noise power spectral density at `output_node`.

Each component declares which noise kinds it can emit
(`Component.SUPPORTED_NOISE`) and exposes them via `noise_sources()`
when the user opts in with `include_noise=` at instantiation. The
solver superposes their contributions:

```
S_V_out(s) = Σ_k  H_k(s) · H_k(-s) · S_k(s)
```

where `H_k(s) = V(output_node) / I_k` is the trans-impedance from the
unit-current k-th noise source to the output, and `S_k` is the
source's one-sided current PSD (A²/Hz). Substitute `s = sp.I * omega`
to evaluate on the imaginary axis.

Returns `(total_psd, per_source_psd)`. The dict is keyed by
`<component>.<kind>` (e.g. `"R1.thermal"`, `"Q1.shot.collector"`) so
contributions can be inspected individually.

Built-in noise models, with `k_B`, `T`, `q` exposed as sympy symbols
in `sycan.mna`:

| Component | Kinds | One-sided PSD |
|-----------|-------|---------------|
| `Resistor` | `thermal` | `4·k_B·T / R` |
| `Diode` | `shot` | `2·q·I_op` |
| `BJT` | `shot` (×2) | `2·q·I_C_op` (collector–emitter), `2·q·I_B_op` (base–emitter) |
| `NMOS_L1`, `PMOS_L1` | `thermal` | `4·k_B·T·γ·g_m`, γ = 2/3 |
| `NMOS_subthreshold`, `PMOS_subthreshold` | `shot` | `2·q·I_op` |
| `Triode` | `thermal` | `4·k_B·T·g_m` |

Operating-point currents (`I_op`, `I_C_op`, `I_B_op`) default to
per-instance symbols if you don't supply them; pass values to pin them.

`include_noise` accepts `None`, a single kind string, a list of kind
strings, or `"all"` (which expands to whatever the class supports).
Unrecognised kinds and valid-but-unsupported kinds both raise
`ValueError` so user mistakes surface at construction time.

```python
c.add(Resistor("R1", "in", "out", R, include_noise="thermal"))
c.add(Capacitor("C1", "out", "0", C))
S, _ = solve_noise(c, "out")           # 4·k_B·T·R / (1 - (s·R·C)²)
```

## Lower-level: `build_mna` and `build_residuals`

For custom solvers (numerical Newton, Monte-Carlo sampling, parametric
sweeps that bypass `sp.simplify`, etc.) you can grab the raw matrices.

- `build_mna(circuit, mode="dc"|"ac", s=None) → (A, x, b)` returns the
  *linear* system. Pass `mode="ac"` and a Laplace variable for AC.
- `build_residuals(circuit, mode="dc"|"ac", s=None) → (x, residuals)`
  returns `A · x − b` plus all `stamp_nonlinear` contributions, ready
  to feed into your own root-finder.

Both helpers walk the same `Component.stamp` / `stamp_nonlinear`
methods that the high-level solvers use, so any custom subclass that
plugs into the unified interface is automatically supported.

## Supporting helpers

These aren't analyses on a `Circuit` but they often appear in the same
workflow:

- **`sycan.network_params`** — closed-form 2-port conversions between
  Z, Y, S, ABCD and T matrices (Pozar conventions, default
  `Z0 = 50 Ω`; pass a symbol or a diagonal `sp.Matrix` for symbolic /
  per-port reference impedances). Useful for reading off S-parameters
  from a Z extracted via `solve_impedance` / `solve_ac`.
- **`sycan.polynomials`** — analog filter prototype transfer functions
  (`butterworth`, `chebyshev1`, `bessel`). Each returns
  `(numerator, denominator)` in a Laplace variable, normalised to
  `|H(0)| = 1` and a 1 rad/s cutoff, so they drop straight into a
  `solve_ac` Bode workflow or a synthesis pass.
- **`sycan.spice.parse` / `parse_file`** — minimal SPICE netlist
  parser; convenient for keeping testbenches as plain text but optional
  — every solver works equally well on a `Circuit` you build directly.
