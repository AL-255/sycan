"""MNA infrastructure: component base, stamping context, and solvers.

Two analysis modes are supported:

* ``"dc"`` — steady-state operating point. Inductors short, capacitors open.
  Components with a ``stamp_nonlinear`` hook (e.g. MOSFETs in sub-threshold)
  contribute transcendental terms and the solver falls back to ``sp.solve``.
* ``"ac"`` — small-signal frequency-domain analysis using a Laplace
  variable ``s``. Capacitors stamp admittance ``sC``; inductors stamp
  ``1/(sL)``. Source AC values override DC values.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional, Union

import sympy as sp

if TYPE_CHECKING:
    from sycan.circuit import Circuit

Value = Union[sp.Expr, int, float, str]

# Specification accepted by ``Component(include_noise=...)``.
NoiseSpec = Union[None, str, list[str], tuple[str, ...], frozenset[str]]

# Recognised noise kinds. Components advertise the subset they emit via
# ``SUPPORTED_NOISE``.
_NOISE_KINDS: frozenset[str] = frozenset({"thermal", "shot", "flicker"})

# Physical-constant symbols used inside symbolic noise spectral densities.
# Exposed at module level so user code can substitute / numerically
# evaluate them (e.g. ``S.subs({k_B: 1.380649e-23, T: 300})``).
k_B: sp.Symbol = sp.Symbol("k_B", positive=True)
T: sp.Symbol = sp.Symbol("T", positive=True)
q: sp.Symbol = sp.Symbol("q", positive=True)


@dataclass
class NoiseSource:
    """Symbolic small-signal noise current source.

    A unit current with one-sided power spectral density ``psd``
    (units: A²/Hz) flowing from ``n_plus`` to ``n_minus`` internally
    (same convention as :class:`CurrentSource`). ``kind`` is one of
    ``"thermal"``, ``"shot"``, ``"flicker"``; ``name`` is a unique
    string of the form ``"<component>.<kind>[.<tag>]"`` so that callers
    can dis-aggregate the contributions returned by :func:`solve_noise`.
    """

    name: str
    kind: str
    n_plus: str
    n_minus: str
    psd: sp.Expr


@dataclass
class StampContext:
    """Mutable state handed to each component's ``stamp`` method."""

    A: sp.Matrix
    b: sp.Matrix
    node_rows: dict[str, int]
    aux_rows: dict[str, int]
    mode: str = "dc"  # "dc" or "ac"
    s: Optional[sp.Expr] = None  # Laplace variable, set in AC mode
    x: Optional[sp.Matrix] = None  # unknown symbols vector (set for nonlinear pass)
    residuals: Optional[list] = None  # nonlinear residuals (set for nonlinear pass)

    def n(self, node: str) -> int:
        """MNA row of a node name; ``-1`` for ground."""
        try:
            return self.node_rows[node]
        except KeyError:
            raise ValueError(f"unknown node {node!r}") from None

    def aux(self, name: str) -> int:
        """MNA row of a component's auxiliary branch current."""
        try:
            return self.aux_rows[name]
        except KeyError:
            raise ValueError(
                f"component {name!r} has no auxiliary branch current "
                "(not a V/E/H/L-in-DC source?)"
            ) from None


class Component(ABC):
    """Netlist element that can stamp itself into an MNA matrix.

    Subclasses that introduce a branch-current unknown set
    ``has_aux = True``. Subclasses that add transcendental current
    contributions (e.g. MOSFETs) set ``has_nonlinear = True`` and
    implement :meth:`stamp_nonlinear``, which is called during the DC
    residual pass.

    Concrete subclasses declare which of their dataclass fields name
    circuit nodes via ``ports`` — a class variable listing attribute
    names. ``Circuit`` walks this list to register a component's nodes
    without hard-coding per-component attribute names.

    Every concrete subclass is auto-registered into ``Component._registry``
    when its module is imported, keyed by class name. The registry is
    queryable via :meth:`Component.available`.
    """

    name: str
    ports: ClassVar[tuple[str, ...]] = ()
    has_aux: ClassVar[bool] = False
    has_nonlinear: ClassVar[bool] = False
    # Subset of ``_NOISE_KINDS`` this component can emit. Defaults to
    # empty (passive ideal element); concrete classes override.
    SUPPORTED_NOISE: ClassVar[frozenset[str]] = frozenset()

    _registry: ClassVar[dict[str, type["Component"]]] = {}

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        # Skip private / abstract bases (leading underscore by convention).
        if cls.__name__.startswith("_"):
            return
        Component._registry[cls.__name__] = cls

    @classmethod
    def available(cls) -> dict[str, type["Component"]]:
        """Return the registry of every concrete Component subclass."""
        return dict(cls._registry)

    def iter_node_names(self) -> Iterator[str]:
        """Yield each circuit node name referenced by this component."""
        for attr in self.ports:
            node = getattr(self, attr, None)
            if node is not None:
                yield node

    def aux_count(self, mode: str) -> int:
        """Number of auxiliary branch currents the component needs in ``mode``."""
        return int(self.has_aux)

    @classmethod
    def _normalize_noise(cls, spec: NoiseSpec) -> frozenset[str]:
        """Validate and canonicalise an ``include_noise=`` argument.

        ``None`` and the empty list disable noise. The string ``"all"``
        expands to whatever ``cls.SUPPORTED_NOISE`` advertises. A single
        kind string or an iterable of kind strings selects those kinds;
        each must be a recognised kind (``"thermal"``, ``"shot"``,
        ``"flicker"``) and supported by the concrete class — otherwise
        a :class:`ValueError` is raised so that user mistakes surface
        early.
        """
        if spec is None:
            return frozenset()
        if isinstance(spec, str):
            if spec == "all":
                return frozenset(cls.SUPPORTED_NOISE)
            requested = frozenset({spec})
        else:
            requested = frozenset(spec)
        invalid = requested - _NOISE_KINDS
        if invalid:
            raise ValueError(
                f"{cls.__name__}: unrecognised noise kinds {sorted(invalid)!r}; "
                f"valid kinds are {sorted(_NOISE_KINDS)!r}"
            )
        unsupported = requested - cls.SUPPORTED_NOISE
        if unsupported:
            raise ValueError(
                f"{cls.__name__}: does not model {sorted(unsupported)!r} "
                f"noise (supported: {sorted(cls.SUPPORTED_NOISE)!r})"
            )
        return requested

    def noise_sources(self) -> list["NoiseSource"]:
        """Return the small-signal noise sources this component emits.

        Default is no-op; concrete components override to emit thermal
        / shot / flicker contributions weighted by the
        :attr:`include_noise` selection.
        """
        return []

    @abstractmethod
    def stamp(self, ctx: StampContext) -> None: ...

    def stamp_nonlinear(self, ctx: StampContext) -> None:
        """Add transcendental terms to ``ctx.residuals`` at solve time.

        Only invoked for DC analysis when the circuit contains at least
        one component with ``has_nonlinear = True``. Default is no-op.
        """
        return None


def build_mna(
    circuit: "Circuit",
    mode: str = "dc",
    s: Optional[sp.Expr] = None,
) -> tuple[sp.Matrix, sp.Matrix, sp.Matrix]:
    """Assemble the linear symbolic MNA system ``A * x = b`` for ``mode``."""
    if mode == "ac" and s is None:
        s = sp.Symbol("s")

    nodes = circuit.nodes
    n = len(nodes)
    aux_owners = [c for c in circuit.components if c.aux_count(mode) > 0]
    m = len(aux_owners)

    A = sp.zeros(n + m, n + m)
    b = sp.zeros(n + m, 1)
    node_rows = {name: idx - 1 for name, idx in circuit._nodes.items()}
    aux_rows = {c.name: n + k for k, c in enumerate(aux_owners)}

    ctx = StampContext(
        A=A, b=b, node_rows=node_rows, aux_rows=aux_rows, mode=mode, s=s
    )
    for c in circuit.components:
        c.stamp(ctx)

    x = sp.Matrix(
        [sp.Symbol(f"V({nm})") for nm in nodes]
        + [sp.Symbol(f"I({c.name})") for c in aux_owners]
    )
    return A, x, b


def build_residuals(
    circuit: "Circuit",
    mode: str = "dc",
    s: Optional[sp.Expr] = None,
) -> tuple[sp.Matrix, list[sp.Expr]]:
    """Assemble the full residual vector ``A*x - b + nonlinear(x)``.

    Useful as a starting point for custom nonlinear solvers (numerical
    Newton, continuation, noise sampling, etc.) that want the raw
    symbolic equations rather than the dict form produced by
    :func:`solve_dc`.
    """
    A, x, b = build_mna(circuit, mode=mode, s=s)
    residuals = list(A * x - b)

    nodes = circuit.nodes
    n = len(nodes)
    aux_owners = [c for c in circuit.components if c.aux_count(mode) > 0]
    node_rows = {name: idx - 1 for name, idx in circuit._nodes.items()}
    aux_rows = {c.name: n + k for k, c in enumerate(aux_owners)}
    ctx = StampContext(
        A=A, b=b, node_rows=node_rows, aux_rows=aux_rows,
        mode=mode, s=s, x=x, residuals=residuals,
    )
    for c in circuit.components:
        if c.has_nonlinear:
            c.stamp_nonlinear(ctx)
    return x, residuals


def solve_dc(circuit: "Circuit", simplify: bool = True) -> dict[sp.Symbol, sp.Expr]:
    """Solve the DC operating point symbolically.

    If any component reports ``has_nonlinear``, the solver builds
    ``residuals = A·x − b`` plus nonlinear contributions and calls
    :func:`sympy.solve`. Otherwise, LU on the linear system.
    """
    A, x, b = build_mna(circuit, mode="dc")
    nonlinear = [c for c in circuit.components if c.has_nonlinear]

    if not nonlinear:
        sol = A.LUsolve(b)
        result = {sym: expr for sym, expr in zip(x, sol)}
    else:
        residuals = list(A * x - b)
        nodes = circuit.nodes
        aux_owners = [c for c in circuit.components if c.aux_count("dc") > 0]
        node_rows = {name: idx - 1 for name, idx in circuit._nodes.items()}
        aux_rows = {c.name: len(nodes) + k for k, c in enumerate(aux_owners)}
        ctx = StampContext(
            A=A,
            b=b,
            node_rows=node_rows,
            aux_rows=aux_rows,
            mode="dc",
            x=x,
            residuals=residuals,
        )
        for c in nonlinear:
            c.stamp_nonlinear(ctx)

        # ``sp.solve`` works well for polynomial nonlinear systems
        # (e.g. plain Shichman-Hodges Level-1 in saturation) and even
        # for transcendental ones once the voltage sources have
        # pinned the unknowns down to single equations
        # (e.g. the subthreshold-MOSFET tests). It *spins forever*,
        # though, on segmented systems where the unknowns are
        # genuinely coupled and a ``Piecewise`` selector picks
        # different branches based on those unknowns — that's the
        # MOSFET_3T inverter case. Detect that combination
        # ("residuals contain Piecewise" AND "no free parameters
        # left to solve symbolically over") and skip straight to
        # damped Newton; everything else still goes through the
        # closed-form symbolic path.
        free_params = set()
        for r in residuals:
            free_params |= r.free_symbols
        free_params -= set(x)
        has_piecewise = any(r.has(sp.Piecewise) for r in residuals)
        try:
            if has_piecewise and not free_params:
                solutions = []
            else:
                solutions = sp.solve(residuals, list(x), dict=True)
        except NotImplementedError:
            solutions = []
        if solutions:
            sol_dict = solutions[0]
            result = {sym: sol_dict.get(sym, sym) for sym in x}
        else:
            # Damped Newton over a numpy-lambdified residual. Plain
            # ``sp.nsolve`` (mpmath's undamped Newton) hangs on
            # MOSFET-style segmented models: when an iterate briefly
            # leaves the physical V_DS ≥ 0 envelope the L1 triode
            # quadratic blows up and Newton can't recover. Damping
            # with line search keeps every step inside the basin
            # of attraction. We also add the standard SPICE GMIN
            # shunt (1 GΩ from every node to ground) so the Jacobian
            # stays conditioned at flat-slope operating points such
            # as L1 saturation with ``lam = 0``.
            import numpy as np
            G_MIN = sp.Float("1e-12")
            reg_residuals = list(residuals)
            for _name, row_idx in node_rows.items():
                reg_residuals[row_idx] += G_MIN * x[row_idx]
            x_list = list(x)
            F_fn = sp.lambdify(
                [x_list], sp.Matrix(reg_residuals), modules="numpy",
            )
            J_fn = sp.lambdify(
                [x_list], sp.Matrix(reg_residuals).jacobian(x_list),
                modules="numpy",
            )
            xv = np.zeros(len(x_list), dtype=float)
            ok = False
            # Line-search trial points routinely probe transcendental
            # branches that overflow ``exp`` — we catch the overflowed
            # iterates via the ``isfinite`` guard below, so the warnings
            # are noise. Suppress them locally instead of letting them
            # leak into the user's output.
            with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
                for _ in range(80):
                    fv = np.asarray(F_fn(xv), dtype=float).reshape(-1)
                    fnorm = float(np.linalg.norm(fv, np.inf))
                    if fnorm < 1e-10:
                        ok = True
                        break
                    Jm = np.asarray(J_fn(xv), dtype=float)
                    # Regularise rank-deficient Jacobians (e.g. a fully
                    # cut-off device makes a row all zeros).
                    Jm = Jm + 1e-12 * np.eye(len(x_list))
                    try:
                        step = np.linalg.solve(Jm, fv)
                    except np.linalg.LinAlgError:
                        step = np.linalg.lstsq(Jm, fv, rcond=None)[0]
                    # Backtracking line search: shrink the step until
                    # the residual norm strictly decreases (or we
                    # exhaust halvings, in which case we accept the
                    # smallest step and try again).
                    alpha = 1.0
                    accepted = False
                    for _ls in range(40):
                        xv_try = xv - alpha * step
                        fv_try = np.asarray(F_fn(xv_try), dtype=float).reshape(-1)
                        if (np.all(np.isfinite(fv_try))
                                and float(np.linalg.norm(fv_try)) < float(np.linalg.norm(fv))):
                            xv = xv_try
                            accepted = True
                            break
                        alpha *= 0.5
                    if not accepted:
                        # Take the tiniest step we tried and continue —
                        # better than spinning at the same iterate.
                        xv = xv - alpha * step
            if not ok:
                raise RuntimeError(
                    "DC solver could not close the nonlinear system. "
                    "Try pinning more node voltages or substituting "
                    "numeric values."
                )
            result = {sym: sp.Float(float(xv[i])) for i, sym in enumerate(x_list)}

    if simplify:
        result = {sym: sp.simplify(expr) for sym, expr in result.items()}
    return result


def solve_impedance(
    circuit: "Circuit",
    port_name: str,
    termination: str = "auto",
    s: Optional[sp.Expr] = None,
    simplify: bool = False,
) -> sp.Expr:
    """Small-signal impedance looking into a named ``Port``.

    A unit AC voltage is applied across the target port and the
    resulting branch current is read out; ``Z = dv/di`` follows.
    Other ports in the netlist are terminated per ``termination``:

    * ``"z"``    — all other ports open (Z-parameter convention).
    * ``"y"``    — all other ports shorted (Y-parameter convention).
    * ``"auto"`` — other *input* ports shorted, *output* ports opened
      (the usual "sources zeroed, loads open" convention for amplifier
      input / output impedance).

    The test source and any termination wires are added to a throw-away
    copy of the circuit, so the caller's circuit is left untouched.
    """
    from sycan.circuit import Circuit
    from sycan.components.basic.port import Port

    if termination not in ("z", "y", "auto"):
        raise ValueError(
            f"termination must be 'z', 'y' or 'auto'; got {termination!r}"
        )

    target_port: Optional[Port] = None
    other_ports: list[Port] = []
    for c in circuit.components:
        if isinstance(c, Port):
            if c.name == port_name:
                target_port = c
            else:
                other_ports.append(c)
    if target_port is None:
        raise ValueError(f"port {port_name!r} not found in circuit")

    # Shallow-copy the circuit: share non-Port components, copy node registry.
    test_circuit = Circuit()
    test_circuit._nodes = dict(circuit._nodes)
    test_circuit.components = [
        c for c in circuit.components if not isinstance(c, Port)
    ]

    # Apply a 1 V AC test source at the target port.
    test_circuit.add_vsource(
        "_Vtest", target_port.n_plus, target_port.n_minus,
        value=0, ac_value=1,
    )

    # Terminate the other ports.
    for p in other_ports:
        short_it = (
            termination == "y"
            or (termination == "auto" and p.role == "input")
        )
        if short_it:
            test_circuit.add_vsource(
                f"_Vshort_{p.name}", p.n_plus, p.n_minus, 0,
            )
        # "z" and "auto" on output/generic ports leave the node open.

    sol = solve_ac(test_circuit, s=s, simplify=False)

    I_test = sol[sp.Symbol("I(_Vtest)")]
    # V_test = +1 (AC); I(V_test) is the SPICE branch current from + to -
    # through the source, which equals the negative of the current the
    # source supplies into the circuit. Hence Z = 1 / (-I_test) = -1/I_test.
    Z = -1 / I_test
    if simplify:
        Z = sp.simplify(Z)
    return Z


def solve_ac(
    circuit: "Circuit",
    s: Optional[sp.Expr] = None,
    simplify: bool = False,
) -> dict[sp.Symbol, sp.Expr]:
    """Solve the small-signal AC response in the Laplace domain.

    Nonlinear components (e.g. MOSFETs) contribute no small-signal model
    yet and are treated as zero-current elements.
    """
    if s is None:
        s = sp.Symbol("s")
    A, x, b = build_mna(circuit, mode="ac", s=s)
    sol = A.LUsolve(b)
    result = {sym: expr for sym, expr in zip(x, sol)}
    if simplify:
        result = {sym: sp.simplify(expr) for sym, expr in result.items()}
    return result


def solve_noise(
    circuit: "Circuit",
    output_node: str,
    s: Optional[sp.Expr] = None,
    simplify: bool = False,
) -> tuple[sp.Expr, dict[str, sp.Expr]]:
    """Output-voltage noise PSD at ``output_node`` in the Laplace domain.

    Walks every component, asks for its :meth:`Component.noise_sources`,
    and superposes their contributions::

        S_V_out(s) = Σ_k  H_k(s) · H_k(-s) · S_k

    where ``H_k(s) = V(output_node) / I_k(s)`` is the trans-impedance
    from the unit-current k-th noise source to the output, and
    ``S_k(s)`` is the source's spectral density. To turn the symbolic
    result into a frequency-domain PSD, substitute ``s = sp.I * omega``.

    Independent V/I sources elsewhere in the circuit are *not* zeroed
    explicitly — their small-signal contribution does not enter the
    transfer-function matrix ``A``, only the right-hand side, and we
    rebuild ``b`` per noise source so they fall away naturally.

    Returns ``(total_psd, per_source_psd)`` where ``per_source_psd``
    maps each :attr:`NoiseSource.name` to its individual contribution.
    """
    if s is None:
        s = sp.Symbol("s")
    A, _x, _b = build_mna(circuit, mode="ac", s=s)
    nodes = circuit.nodes
    if output_node not in nodes:
        raise ValueError(
            f"output node {output_node!r} not in circuit nodes {nodes!r}"
        )
    out_idx = nodes.index(output_node)

    contributions: dict[str, sp.Expr] = {}
    total: sp.Expr = sp.S.Zero
    for c in circuit.components:
        for src in c.noise_sources():
            for endpoint in (src.n_plus, src.n_minus):
                if endpoint not in circuit._nodes:
                    raise ValueError(
                        f"noise source {src.name!r}: node {endpoint!r} not "
                        "registered in circuit"
                    )
            b = sp.zeros(A.shape[0], 1)
            ip = circuit._nodes[src.n_plus] - 1
            im = circuit._nodes[src.n_minus] - 1
            if ip >= 0:
                b[ip] -= 1
            if im >= 0:
                b[im] += 1
            sol = A.LUsolve(b)
            H = sol[out_idx]
            H_sq = H * H.subs(s, -s)
            contrib = H_sq * src.psd
            contributions[src.name] = contrib
            total = total + contrib

    if simplify:
        total = sp.simplify(total)
        contributions = {k: sp.simplify(v) for k, v in contributions.items()}
    return total, contributions
