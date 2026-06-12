# Changelog

All notable changes to SYCAN are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

#### Symbolic transient analysis
- New `solve_transient(circuit, outputs=None, s=None, t=None,
  simplify=False, initial_conditions=None, noconds=True)` solver:
  builds the Laplace-domain MNA system in the new `mode="tran"` and
  inverse-Laplace-transforms the selected unknowns into exact
  time-domain expressions. Returns a `TransientResult` dataclass with
  both `s_solution` (always available) and `t_solution` (entries the
  CAS cannot invert are preserved as unevaluated
  `InverseLaplaceTransform` objects). Both are exported from `sycan`.
- `mode="tran"` stamping across components: linear dynamic elements
  (C, L, mutual coupling, varactor, TLINE, controlled sources,
  behavioural elements, blocks) stamp exactly as in AC; nonlinear
  devices contribute their small-signal AC stamps (small-signal
  transient around the operating point).
- Source waveforms (`"sine"` / `"pulse"` / `"exp"`) stamp their
  Laplace transforms in `tran`; a source without a waveform stamps its
  DC `value` as a step at `t = 0` (`value/s`). New free helpers
  `waveform_laplace(source, s)` and `waveform_time(source, t)`
  (time helper uses `Heaviside` for delayed segments).
- Initial conditions: `add_capacitor(..., ic=V0)` /
  `add_inductor(..., ic=I0)` element fields plus a solver-time
  `initial_conditions={...}` override map (overrides win; unknown
  names or non-storage components raise `ValueError`). Capacitor
  polarity is `v0 = V(n+) − V(n−)`; inductor current is positive
  `n+ → n−`. Coupled inductors stamp the `−M·i_j0` cross terms.
- SPICE parser / writer support for transient sources and ICs:
  `SIN(vo va freq [td theta phase])` (phase in degrees per SPICE,
  offset `vo` becomes the DC `value`), `PULSE(v1 v2 [td tr tf pw per])`
  (ideal-edge single-shot; non-zero `tr`/`tf` and any `per` are
  rejected with a clear message), `EXP(v1 v2 td1 tau1 [td2 tau2])`,
  and `IC=` on C / L lines. `to_spice` emits all of these, so
  waveform sources round-trip (previously `NotImplementedError`).
- SymEngine backend: `apart`, `inverse_laplace_transform`, and
  `laplace_transform` bridges. The Laplace bridges treat the time
  variable as positive (symengine symbols carry no assumptions);
  results containing `Heaviside` stay sympy-side. The transient test
  suite passes under `SYCAN_CAS_BACKEND=symengine` except two
  canonical-form tests that need positive waveform parameters
  (skipped with reasons in `tests/conftest.py`).
- Tests under `tests/transient/` (solver, parser/writer round-trip,
  waveform-helper consistency); REPL demos ("RC step response",
  "Natural responses (ICs)"); docs in `docs/analysis.md`,
  `sphinx/getting_started.rst`, `STRUCTURE.md`.

## [0.1.8] — 2026-05-11

### Added

#### Assumption engine
- New `sycan.assumptions` module with first-class symbolic constraints:
  - `Limit(symbol, target)` — fold a free symbol via `cas.limit()`
    (e.g. op-amp gain `A → ∞` collapses closed-loop expressions to
    their ideal form).
  - `MuchGreater(big, small)` and `MuchLess(small, big)` — relative-
    magnitude assumptions; if either side is a bare symbol the engine
    uses the simpler `→ ∞` / `→ 0` limit, otherwise an
    ε-substitution `small = ε·big, ε → 0`.
  - `Approximate(symbol, value)` — tracked substitution without taking
    a limit.
  - `Region(component, region)` — declare a device's operating region
    (no equation change); the checker re-evaluates the claim against
    the solved operating point. Recognised regions: MOSFET
    saturation/triode/cutoff, BJT forward-active/reverse-active/
    saturation/cutoff, diode forward/reverse. Polarity is handled
    automatically (PMOS, PNP).
- `CheckResult` carries pass/fail, the violating inequality, and a
  `measured` dict of the computed quantities (`V_GS_eff`, `V_DS_eff`,
  `V_TH`, `V_OV`, `V_BE_eff`, `V_BC_eff`, …).
- Module helpers: `apply_assumptions`, `check_assumptions`,
  `format_check_report`, `violations`.
- `Circuit.assume(*assumptions)` plus four sugar methods —
  `assume_limit`, `assume_much_greater`, `assume_much_less`,
  `assume_region` — and `Circuit.check_assumptions(solution)`.

#### Unified solver
- New `sycan.solve(circuit, *, mode='dc'|'ac', s=None, simplify=False,
  assume=None)` entry point. For LTI circuits, `mode='dc'` literally
  builds the AC matrix and substitutes `s = 0` (the formal *DC = AC at
  ω → 0* unification, with a `cas.limit` fallback for expressions
  singular at zero). Circuits containing nonlinear devices fall back
  to the existing `solve_dc` path.
- `solve_dc` and `solve_ac` accept a new `assume=` kwarg that combines
  with circuit-attached assumptions.

#### Hierarchical design
- Parameterised subcircuits: `SubCircuit.params: dict[str, Value]`
  substitutes matching `cas.Symbol` placeholders on every cloned leaf
  at expansion time. Outer params propagate into nested SubCircuits
  unless the inner instance overrides the same key.
- `Circuit.group(components, name, …)` wraps a slice of a circuit's
  components into a new SubCircuit *in place* — derives external pins
  from cross-boundary node usage, namespaces internal-only nodes, and
  rebuilds the parent's node table so the MNA matrix stays well-formed.
- `Circuit.print_hierarchy` (and a top-level `sycan.print_hierarchy`
  helper) now displays each SubCircuit's `PARAMS:` inline.

#### SPICE I/O
- New `to_spice(circuit) -> str` writer (and `write_file(circuit,
  path)`) emits one `.subckt` block per distinct body identity with
  per-instance `PARAMS:` overrides on the `X` lines. Built-in `OPAMP`
  round-trips through the existing `X … OPAMP A` form.
- SPICE parser learns `.SUBCKT name pins… PARAMS: k=v …` (defaults)
  and `Xinst pins… name PARAMS: k=v …` (per-instance overrides),
  merging them into the SubCircuit's `params` dict.

#### Autodraw
- `autodraw(collapse=…)` parameter accepts a dotted group path (or a
  list of them) and replaces matching SubCircuits with a single
  purple-outlined placeholder rectangle. Unknown paths raise
  `ValueError` listing the actual hierarchy paths.
- Group-aware layout: every leaf carries its enclosing-SubCircuit
  chain via a `_group_path` tag set by `SubCircuit.expand_leaves`.
  The SA cost adds a column-span penalty proportional to group size
  so members cluster into adjacent columns; column widths reserve
  room for the group rectangle's margin (and `_compact_blanks`
  honours it) so the dashed bounding box never overlaps a neighbour.
- Nested groups render as concentric rectangles with a per-level
  padding step (default 6 px) so outer rectangles always stand out
  from the inner ones.

#### Documentation
- New `sphinx/assumptions.rst` page covering motivation, all four
  assumption types, attached vs. inline styles, the unified solver,
  and the post-solve checker — wired into the toctree.
- Three new REPL presets under an "Assumptions" category in
  `docs/repl/examples/manifest.json`: ideal op-amp limit, divider
  asymptotes via `MuchGreater`, and a MOSFET region check that
  catches a biasing mistake.

### Changed
- Autodraw flattens via `circuit.flat_components()` rather than
  walking `circuit.components` directly, so any hierarchy renders
  correctly.

### Tests
- 27 new tests under `tests/assumptions/` (limit collapse,
  much-greater simplification, MOSFET / BJT region checks, DC ≡ AC@s=0
  unification).
- 13 in `tests/blocks/test_subcircuit_params.py` (parameter
  propagation, nested overrides, parser PARAMS handling, parse↔to_spice
  round-trip).
- 13 in `tests/blocks/test_group.py` (group plumbing and behaviour
  preservation).
- 23 in `tests/drawing/test_autodraw_groups.py` (group bounding boxes,
  nested concentric rendering, depth-step padding, collapse rendering,
  pin clearance against neighbours). Visual diagrams 20–31 land under
  `tests/drawing/diagrams/` for spot-checking.
- Suite total: 408 passing.

### Pre-existing infrastructure (also shipped in 0.1.8)
- `main()` CLI entry point with `--version` and `parse` subcommand.
- PyPI classifiers, license metadata, and `keywords` in
  `pyproject.toml`.
- Optional `[symengine]` extra for the faster CAS backend.
- `sycan.check`, `sycan.headroom`, `sycan.plot_util`, `sycan.cas`,
  and `sycan.components.blocks` in the Sphinx API reference.
- Python 3.11 / 3.12 / 3.13 matrix in CI.
- Expanded `README.md` with installation, quick-start example, and an
  analysis-mode reference table.
- Pre-built REPL wheel removed from the repo (`docs/repl/sycan-*.whl`
  is now built fresh by CI and `run_webpage.sh`).

## [0.1.7] — 2026

### Added
- DC sweep (`solve_dc_sweep`).
- Transfer-function solver (`solve_tf`).
- Sensitivity analysis (`solve_sensitivity`).
- Behavioral voltage and current sources.
- Varactor and voltage-controlled switch components.
- Design rule check / ERC (`check_circuit`).
- JFET model (NJFET / PJFET).
- Pole–zero analysis (`solve_pz`).
- Mutual inductance (K) coupling.

### Changed
- Sedra schematic editor: mobile device improvements, glyph updates.

## [0.1.x] — earlier

### Added
- AC small-signal models for diode and BJT (hybrid-π).
- Flicker (1/f) noise for MOSFET L1/4T/subthreshold and BJT.
- Lossy transmission line.
- First-order op-amp (`OPAMP1`) with finite GBW and output impedance.
- PULSE and EXP waveform sources (Laplace-domain stamps).
- A* autorouter for `autodraw`.
- Sedra in-browser schematic editor (TypeScript port).
- SubCircuit support.
- SymEngine backend (multi-backend CAS abstraction).
- Linear-system modelling blocks (Gain, Integrator, Summer, Quantizer,
  TransferFunction). Type-II PLL and ΣΔ modulator REPL examples.
- Headroom analysis (`solve_headroom`).
- Glyph reference page in Sphinx docs.
- MOSFET 4T model and NAND2 stacking-effect demo.
- MOSFET 3T model bridging L1 and subthreshold.
- Engineering-format helpers in `plot_util`.
- Schematic back-annotation.
- Retry / double-pass simulated annealing for `autodraw` to reduce wire
  overlap.
