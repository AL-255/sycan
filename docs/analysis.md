# SYCAN analysis methods

This is a short reference for every analysis methods _SYCAN_ supports. All
solvers operate on a `Circuit` populated with `Component` instances and
return CAS expressions (sympy by default — see `sycan.cas` for backend
selection), so results stay symbolic until you `subs(...)` or `.evalf()`
them.

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
  e.g. a diode, BJT, MOSFET, or triode — fall back to the CAS solver
  (`sp.solve`, where `sp` is `sycan.cas`) on the residual vector
  `A · x − b + Σ stamp_nonlinear`. A `RuntimeError` is raised if the
  CAS fails to close the system; pin more nodes with explicit voltage
  sources or substitute numeric parameters when that happens.

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

Note: source `waveform=` specs (`"sine"` / `"pulse"` / `"exp"`) also
stamp their Laplace transforms in AC mode. That is legacy
compatibility — `solve_transient` below is the intended API for
time-domain responses.

## Symbolic transient — `solve_transient(circuit, outputs=None, s=None, t=None, simplify=False, initial_conditions=None, noconds=True)`

Exact time-domain response of an LTI circuit: the Laplace-domain MNA
system is solved in `mode="tran"` and the selected unknowns are
inverse-Laplace-transformed into closed-form expressions in `t`. This
is symbolic Laplace analysis, not numeric time stepping.

In `tran` mode:

- **Sources** stamp the Laplace transform of their `waveform=` spec
  (`"sine"`, `"pulse"`, `"exp"`); a source without a waveform stamps
  its DC `value` switched on at `t = 0`, i.e. `value/s`. `ac_value`
  is ignored — it is an AC-phasor concept.
- **Capacitors / inductors** stamp their usual `s·C` / `s·L` dynamic
  terms plus initial-condition injections: a capacitor with initial
  voltage `v0` adds `+C·v0` / `−C·v0` current injections at its
  terminals; an inductor with initial current `i0` adds `−L·i0` on
  its KVL row (from `V = L·(s·I − i0)`). Coupled inductors pick up
  the corresponding `−M·i_j0` cross terms.
- **Nonlinear devices** contribute their small-signal AC stamps only,
  so the result is a *small-signal transient around the supplied
  operating point* — not a nonlinear large-signal simulation.

Initial conditions can be set per element or at solve time (the
solve-time map wins):

```python
c.add_capacitor("C1", "out", "0", C, ic=V0)   # v0 = V(n+) − V(n−) at t = 0⁻
c.add_inductor("L1", "in", "out", L, ic=I0)   # i0 flows n+ → n− through L
solve_transient(c, initial_conditions={"C1": V0, "L1": I0})
```

Unknown names, or names of components that are not capacitors /
inductors, raise `ValueError`.

`outputs` selects what gets inverse-transformed: node-name strings map
to `Symbol("V(<node>)")`, symbols such as `Symbol("I(L1)")` are used
directly, and `None` transforms every unknown. The returned
`TransientResult` carries both domains:

- `s_solution` — the full Laplace-domain solution (always available),
- `t_solution` — time-domain expressions for the selected outputs,
- `s`, `t` — the variables used (`t` is created positive so
  `Heaviside(t)` factors collapse to 1; delayed edges keep explicit
  `Heaviside(t − td)` terms).

```python
from sycan import Circuit, solve_transient, cas

R, C, Vstep = cas.symbols("R C Vstep", positive=True)

c = Circuit("rc_step")
c.add_vsource("V1", "in", "0", 0, waveform="pulse", v1=0, v2=Vstep, td=0, pw=cas.oo)
c.add_resistor("R1", "in", "out", R)
c.add_capacitor("C1", "out", "0", C)

tran = solve_transient(c, outputs=["out"], simplify=True)
print(tran.t_solution[cas.Symbol("V(out)")])   # Vstep - Vstep*exp(-t/(C*R))
```

Limitations: exact inversion is CAS-limited — when the CAS cannot
close the transform the entry is preserved as an unevaluated
`InverseLaplaceTransform` (and the raw `s_solution` is still there);
partial fractions (`apart`) are applied automatically beforehand to
maximise the hit rate. Transmission-line (`TLINE`) responses invert
only as far as the CAS can handle their transcendental s-domain
expressions. Inverse Laplace transforms are computed by sympy — under
the symengine backend the operation bridges to sympy, and results
containing `Heaviside` stay sympy-side. The free helpers
`waveform_laplace(source, s)` / `waveform_time(source, t)` expose the
stamped transform and its time-domain counterpart for docs and
validation.

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

Built-in noise models, with `k_B`, `T`, `q` exposed as CAS symbols
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
  `Z0 = 50 Ω`; pass a symbol or a diagonal `sp.Matrix` from
  `sycan.cas` for symbolic / per-port reference impedances). Useful
  for reading off S-parameters from a Z extracted via
  `solve_impedance` / `solve_ac`.
- **`sycan.polynomials`** — analog filter prototype transfer functions
  (`butterworth`, `chebyshev1`, `bessel`). Each returns
  `(numerator, denominator)` in a Laplace variable, normalised to
  `|H(0)| = 1` and a 1 rad/s cutoff, so they drop straight into a
  `solve_ac` Bode workflow or a synthesis pass.
- **`sycan.spice.parse` / `parse_file`** — minimal SPICE netlist
  parser; convenient for keeping testbenches as plain text but optional
  — every solver works equally well on a `Circuit` you build directly.
