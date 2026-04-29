"""Headroom analysis — symbolic input range that keeps every MOSFET saturated.

Given a circuit and one *input axis* — either a single independent
source whose value is swept, or a group of sources whose values are
all tied to one common scalar variable — :func:`solve_headroom`
returns the symbolic interval of that variable for which every
MOSFET in the circuit is in saturation.

The analysis is fully symbolic. For each MOSFET it builds two
saturation predicates straight from the device equations,

* ``c1 = V_GS_eff - V_TH(V_SB) > 0``        (above threshold, in inversion)
* ``c2 = V_DS_eff - (V_GS_eff - V_TH) >= 0`` (V_DS past the overdrive knee)

and ties them to a symbolic operating-point that is solved with the
saturation form of every device's drain current — no triode / weak
inversion branches, only ``I_D = (1/2) β (V_GS_eff − V_TH)² (1 + λ V_DS_eff)``
with the long-channel body-effect threshold for 4T cells. Substituting
the solved node voltages turns each predicate into an expression in
the input variable (and any leftover symbolic parameters), so the
interval edges are obtained by ``sp.solve`` of each predicate against
the input, not by sweeping. If the answer is purely numeric, you get
numbers; if some parameter is left as a symbol, the boundary is a
sympy expression in those symbols.

Typical use::

    from sycan import parse, solve_headroom
    c = parse(\"\"\"...netlist with MOSFETs and a single Vin...\"\"\")
    result = solve_headroom(c, "Vin")
    print(result)                          # symbolic / numeric interval
    print(result.predicates["MN"])         # what MN demands of x
    print(result.boundaries)               # per-device edge values

For a differential pair, the input axis is one symbol :math:`V_{id}`
that drives two physical sources::

    V_id = sp.Symbol("V_id", real=True)
    result = solve_headroom(
        c,
        sources={"Vinp": Rational(9,10) + V_id/2,
                 "Vinm": Rational(9,10) - V_id/2},
        var=V_id,
    )
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional, Union

import sympy as sp

from sycan.circuit import Circuit
from sycan.components.active.mosfet_l1 import _MOSFET_L1
from sycan.components.active.mosfet_4t import _MOSFET_4T
from sycan.components.basic.current_source import CurrentSource
from sycan.components.basic.voltage_source import VoltageSource
from sycan.mna import build_mna


SourceSpec = Union[str, Mapping[str, sp.Expr]]


@dataclass
class HeadroomResult:
    """Symbolic outcome of a :func:`solve_headroom` call.

    Attributes
    ----------
    var
        The input variable the analysis sweeps.
    node_voltages
        Symbolic operating-point map ``{V(node): expr_in_var}``,
        solved with each MOSFET pinned to its saturation drain
        current.
    predicates
        ``{device_name: [c1, c2]}`` — for each MOSFET, the two
        saturation predicates (must be ``> 0`` for ``c1`` and
        ``>= 0`` for ``c2``). Substitute concrete values to check
        margin.
    boundaries
        ``[(device, kind, var_value), ...]`` — every place a
        predicate crosses zero, in symbolic form, with the device
        name and which predicate (``"threshold"`` for ``c1``,
        ``"overdrive"`` for ``c2``).
    interval
        ``(low, high)`` — symbolic edges of the widest contiguous
        all-saturation interval, or ``None`` if no interval is
        consistent (e.g. a fixed bias kills one device regardless
        of the input).
    binding
        ``{"low": device, "high": device}`` — the device that sets
        each interval edge.
    """

    var: sp.Symbol
    node_voltages: dict[sp.Symbol, sp.Expr]
    predicates: dict[str, list[sp.Expr]]
    boundaries: list[tuple[str, str, sp.Expr]]
    interval: Optional[tuple[sp.Expr, sp.Expr]]
    binding: dict[str, Optional[str]]

    def __bool__(self) -> bool:
        return self.interval is not None

    def __repr__(self) -> str:
        if self.interval is None:
            return f"<HeadroomResult: no interval; var={self.var}>"
        lo, hi = self.interval
        return (
            f"<HeadroomResult {self.var} ∈ [{lo}, {hi}]  "
            f"(low: {self.binding['low']}, high: {self.binding['high']})>"
        )

    def summary(self) -> str:
        """Multi-line human-readable report."""
        lines = [f"Headroom analysis on {self.var}:"]
        if self.interval is None:
            lines.append("  no input value puts every MOSFET in saturation.")
        else:
            lo, hi = self.interval
            lines.append(
                f"  saturation interval: {self.var} ∈ [{lo}, {hi}]"
            )
            lines.append(
                f"  binding devices: low → {self.binding['low']}, "
                f"high → {self.binding['high']}"
            )
        lines.append("  per-device saturation predicates (must be ≥ 0):")
        for dev, conds in self.predicates.items():
            lines.append(f"    {dev}:")
            for label, expr in zip(("threshold", "overdrive"), conds):
                lines.append(f"      {label}: {expr}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Saturation-form drain current and the (c1, c2) predicates.
# ---------------------------------------------------------------------------
def _sat_current(m, V_GS: sp.Expr, V_DS: sp.Expr, V_BS: Optional[sp.Expr]) -> sp.Expr:
    pol = sp.Integer(1) if m.polarity == "N" else sp.Integer(-1)
    V_GS_eff = pol * V_GS
    V_DS_eff = pol * V_DS
    beta = m.mu_n * m.Cox * m.W / m.L
    if isinstance(m, _MOSFET_4T):
        V_SB_eff = -pol * V_BS
        V_TH = m.V_TH0 + m.gamma * (sp.sqrt(m.phi + V_SB_eff) - sp.sqrt(m.phi))
        lam = m.lam
    else:
        V_TH = m.V_TH
        lam = m.lam
    return pol * sp.Rational(1, 2) * beta * (V_GS_eff - V_TH) ** 2 * (1 + lam * V_DS_eff)


def _saturation_predicates(
    m,
    V_GS: sp.Expr,
    V_DS: sp.Expr,
    V_BS: Optional[sp.Expr],
) -> tuple[sp.Expr, sp.Expr]:
    pol = sp.Integer(1) if m.polarity == "N" else sp.Integer(-1)
    V_GS_eff = pol * V_GS
    V_DS_eff = pol * V_DS
    if isinstance(m, _MOSFET_4T):
        V_SB_eff = -pol * V_BS
        V_TH = m.V_TH0 + m.gamma * (sp.sqrt(m.phi + V_SB_eff) - sp.sqrt(m.phi))
    else:
        V_TH = m.V_TH
    c1 = V_GS_eff - V_TH                 # > 0  : strong inversion
    c2 = V_DS_eff - (V_GS_eff - V_TH)    # >= 0 : V_DS past the knee
    return c1, c2


# ---------------------------------------------------------------------------
# Saturation-only DC residuals — replaces solve_dc's Piecewise stamps so
# sp.solve can close the system in closed form.
# ---------------------------------------------------------------------------
def _build_sat_residuals(circuit: Circuit) -> tuple[sp.Matrix, list[sp.Expr]]:
    A, x, b = build_mna(circuit, mode="dc")
    residuals = list(A * x - b)
    node_rows = {nm: idx - 1 for nm, idx in circuit._nodes.items()}

    def V(node: str) -> sp.Expr:
        idx = node_rows.get(node, 0)
        return x[idx] if idx >= 0 else sp.Integer(0)

    for c in circuit.components:
        if not isinstance(c, (_MOSFET_L1, _MOSFET_4T)):
            continue
        d = node_rows.get(c.drain, -1)
        s_idx = node_rows.get(c.source, -1)
        V_GS = V(c.gate) - V(c.source)
        V_DS = V(c.drain) - V(c.source)
        if isinstance(c, _MOSFET_4T):
            V_BS = V(c.bulk) - V(c.source)
            I_D = _sat_current(c, V_GS, V_DS, V_BS)
        else:
            I_D = _sat_current(c, V_GS, V_DS, None)
        if d >= 0:
            residuals[d] += I_D
        if s_idx >= 0:
            residuals[s_idx] -= I_D
    return x, residuals


# ---------------------------------------------------------------------------
# Source-spec parsing.
# ---------------------------------------------------------------------------
def _resolve_sources(
    circuit: Circuit,
    sources: SourceSpec,
    var: Optional[sp.Symbol],
) -> tuple[sp.Symbol, list[tuple[Union[VoltageSource, CurrentSource], sp.Expr]]]:
    name_to_src = {
        c.name: c
        for c in circuit.components
        if isinstance(c, (VoltageSource, CurrentSource))
    }

    if isinstance(sources, str):
        if sources not in name_to_src:
            raise ValueError(
                f"source {sources!r} not found in circuit "
                f"(known: {sorted(name_to_src)!r})"
            )
        v = var if var is not None else sp.Symbol(sources, real=True)
        return v, [(name_to_src[sources], v)]

    if not isinstance(sources, Mapping) or not sources:
        raise TypeError(
            "sources must be a source name (str) or a non-empty mapping "
            "{source_name: sympy expression in the input variable}"
        )
    pairs: list[tuple[Union[VoltageSource, CurrentSource], sp.Expr]] = []
    free_syms: set[sp.Symbol] = set()
    for name, expr in sources.items():
        if name not in name_to_src:
            raise ValueError(
                f"source {name!r} not found in circuit "
                f"(known: {sorted(name_to_src)!r})"
            )
        e = sp.sympify(expr)
        pairs.append((name_to_src[name], e))
        free_syms |= e.free_symbols

    if var is None:
        # The "one independent input variable" rule: every source's
        # expression must reduce to a function of one common symbol —
        # constants OK, more than one shared free symbol is rejected.
        per_expr_vars = [
            e.free_symbols
            for _, e in pairs
            if e.free_symbols
        ]
        if not per_expr_vars:
            raise ValueError(
                "all source expressions are constants; nothing to sweep"
            )
        candidates = set.intersection(*per_expr_vars)
        # Drop symbols that are *only* in some of the expressions —
        # those are circuit parameters, not the swept input.
        unique = [s for s in candidates if all(s in v for v in per_expr_vars)]
        if len(unique) != 1:
            raise ValueError(
                "could not infer the input variable; pass var=... explicitly. "
                f"shared free symbols: {candidates}"
            )
        var = unique[0]
    return var, pairs


# ---------------------------------------------------------------------------
# Combine per-device boundaries into the widest all-saturation interval.
# ---------------------------------------------------------------------------
def _direction_at(expr: sp.Expr, var: sp.Symbol, point: sp.Expr) -> int:
    """Sign of dexpr/dvar at ``point`` — +1 / -1 / 0."""
    d = sp.simplify(sp.diff(expr, var).subs(var, point))
    try:
        d_num = float(d)
    except (TypeError, ValueError):
        # Symbolic params left over — assume monotone, ask sp for sign.
        sign = sp.sign(d)
        if sign == sp.Integer(1):
            return 1
        if sign == sp.Integer(-1):
            return -1
        return 0
    if d_num > 0:
        return 1
    if d_num < 0:
        return -1
    return 0


def _classify_boundary(
    expr: sp.Expr,
    var: sp.Symbol,
    point: sp.Expr,
) -> Optional[str]:
    """Is ``point`` a lower bound, upper bound, or neither?"""
    direction = _direction_at(expr, var, point)
    # expr > 0 region:
    #   if dexpr/dvar > 0 at the root: expr is increasing, so root is the
    #     lower boundary of expr > 0 — it's a *lower* bound on var.
    #   if dexpr/dvar < 0 at the root: it's an *upper* bound on var.
    if direction > 0:
        return "low"
    if direction < 0:
        return "high"
    return None


def _solve_real_roots(expr: sp.Expr, var: sp.Symbol) -> list[sp.Expr]:
    try:
        sols = sp.solve(sp.Eq(sp.together(expr), 0), var)
    except (NotImplementedError, sp.PolynomialError):
        return []
    out: list[sp.Expr] = []
    for s in sols:
        if not isinstance(s, sp.Expr):
            continue
        # Reject complex / non-real roots when we can prove it.
        if s.is_real is False:
            continue
        # If the candidate is purely numeric, double-check by
        # numeric reduction — sympy sometimes leaves I-laden cube
        # roots whose imaginary parts cancel only after radical
        # simplify, and we'd rather drop them than carry
        # un-comparable expressions through the interval logic.
        if not s.free_symbols:
            try:
                val = complex(s)
            except (TypeError, ValueError):
                continue
            if abs(val.imag) > 1e-9:
                continue
            out.append(sp.Float(val.real))
            continue
        out.append(s)
    return out


def _interval_from_boundaries(
    var: sp.Symbol,
    predicates: dict[str, list[sp.Expr]],
) -> tuple[
    Optional[tuple[sp.Expr, sp.Expr]],
    list[tuple[str, str, sp.Expr]],
    dict[str, Optional[str]],
]:
    boundaries: list[tuple[str, str, sp.Expr]] = []
    lower_candidates: list[tuple[sp.Expr, str]] = []
    upper_candidates: list[tuple[sp.Expr, str]] = []

    for dev, (c1, c2) in predicates.items():
        for label, cond in (("threshold", c1), ("overdrive", c2)):
            if not cond.has(var):
                # var-independent predicate: either always true or
                # always false; doesn't pin the interval but a "false"
                # constant kills the interval entirely. We let the
                # final consistency check (after combining edges)
                # surface that.
                continue
            roots = _solve_real_roots(cond, var)
            for r in roots:
                boundaries.append((dev, label, r))
                kind = _classify_boundary(cond, var, r)
                if kind == "low":
                    lower_candidates.append((r, dev))
                elif kind == "high":
                    upper_candidates.append((r, dev))

    binding: dict[str, Optional[str]] = {"low": None, "high": None}
    if not lower_candidates and not upper_candidates:
        return None, boundaries, binding

    def _pick_extreme(cands, op):
        # Among numeric candidates we just take the actual max / min;
        # otherwise we fall back to sympy's Min / Max.
        if not cands:
            return None, None
        numeric = []
        for r, d in cands:
            try:
                numeric.append((float(r), r, d))
            except (TypeError, ValueError):
                numeric = None  # type: ignore[assignment]
                break
        if numeric is not None:
            picked = op(numeric, key=lambda t: t[0])
            return picked[1], picked[2]
        # Symbolic — return the Max/Min expression with no clear binder.
        if op is max:
            return sp.Max(*[r for r, _ in cands]), None
        return sp.Min(*[r for r, _ in cands]), None

    low, low_dev = _pick_extreme(lower_candidates, max)
    high, high_dev = _pick_extreme(upper_candidates, min)
    binding["low"] = low_dev
    binding["high"] = high_dev

    if low is None or high is None:
        # One side is unbounded within the analysis — return whichever
        # bound exists, leaving the other open.
        if low is not None and high is None:
            return (low, sp.oo), boundaries, binding
        if high is not None and low is None:
            return (-sp.oo, high), boundaries, binding
        return None, boundaries, binding

    # Numeric sanity check — if both sides are numbers and crossed, the
    # interval is empty.
    try:
        if float(low) >= float(high):
            return None, boundaries, binding
    except (TypeError, ValueError):
        pass
    return (low, high), boundaries, binding


# ---------------------------------------------------------------------------
# Public entry point.
# ---------------------------------------------------------------------------
def solve_headroom(
    circuit: Circuit,
    sources: SourceSpec,
    var: Optional[sp.Symbol] = None,
    simplify: bool = True,
) -> HeadroomResult:
    """Symbolic headroom: input range that keeps every MOSFET in saturation.

    Parameters
    ----------
    circuit
        A :class:`~sycan.circuit.Circuit` containing one or more
        MOSFETs. Sub-threshold-only devices and BJTs are ignored.
    sources
        Either the *name* of one independent voltage / current source —
        in which case the source's value is replaced by ``var`` (or by
        a freshly minted symbol named after the source) — or a
        ``{name: sympy_expression}`` mapping. Every expression in the
        mapping must depend on the same single ``var`` (other free
        symbols are taken as circuit parameters).
    var
        The swept input variable. Optional for the single-source form;
        for the dict form, it is auto-detected as the unique symbol
        common to all expressions, or pass it explicitly to override.
    simplify
        Run ``sp.simplify`` on the operating-point voltages and the
        saturation predicates before returning them. Off for circuits
        whose closed forms are big and the simplification cost is not
        worth the readability gain.

    Returns
    -------
    HeadroomResult
        See the dataclass for fields. The most useful one is
        :attr:`~HeadroomResult.interval` — a ``(low, high)`` pair of
        sympy expressions in the swept variable's coefficients.
    """
    mosfets = [
        c for c in circuit.components
        if isinstance(c, (_MOSFET_L1, _MOSFET_4T))
    ]
    if not mosfets:
        raise ValueError(
            "circuit has no MOSFETs — headroom analysis needs at least one "
            "transistor whose saturation region can be checked."
        )

    var, pairs = _resolve_sources(circuit, sources, var)

    # Mutate the source values for the symbolic solve, then put them
    # back when we're done — keep the caller's circuit pristine.
    originals = [(src, src.value) for src, _ in pairs]
    try:
        for src, expr in pairs:
            src.value = sp.sympify(expr)
        x, residuals = _build_sat_residuals(circuit)
        try:
            sols = sp.solve(residuals, list(x), dict=True)
        except (NotImplementedError, sp.PolynomialError) as exc:
            raise RuntimeError(
                "could not solve the saturation-form DC system in closed "
                "form (sp.solve gave up). The headroom analysis only "
                "supports circuits sp.solve can close — typically L1 or "
                "small 3T networks. Underlying error: " + str(exc)
            ) from exc
        if not sols:
            raise RuntimeError(
                "no saturation-form DC solution exists for this circuit; "
                "the headroom interval is therefore empty."
            )
        sol = sols[0]
        node_voltages = {sym: sol.get(sym, sym) for sym in x}
    finally:
        for src, val in originals:
            src.value = val

    # Build saturation predicates per device, substituting the solved
    # node voltages so they end up as functions of ``var`` (and any
    # leftover symbolic params).
    predicates: dict[str, list[sp.Expr]] = {}
    sym_subs = node_voltages
    for m in mosfets:
        V_g = sym_subs.get(sp.Symbol(f"V({m.gate})"), sp.Integer(0)) if m.gate != "0" else sp.Integer(0)
        V_d = sym_subs.get(sp.Symbol(f"V({m.drain})"), sp.Integer(0)) if m.drain != "0" else sp.Integer(0)
        V_s = sym_subs.get(sp.Symbol(f"V({m.source})"), sp.Integer(0)) if m.source != "0" else sp.Integer(0)
        if isinstance(m, _MOSFET_4T):
            V_b = sym_subs.get(sp.Symbol(f"V({m.bulk})"), sp.Integer(0)) if m.bulk != "0" else sp.Integer(0)
            V_BS = V_b - V_s
        else:
            V_BS = None
        c1, c2 = _saturation_predicates(m, V_g - V_s, V_d - V_s, V_BS)
        if simplify:
            c1 = sp.simplify(c1)
            c2 = sp.simplify(c2)
        predicates[m.name] = [c1, c2]

    if simplify:
        node_voltages = {k: sp.simplify(v) for k, v in node_voltages.items()}

    interval, boundaries, binding = _interval_from_boundaries(var, predicates)

    return HeadroomResult(
        var=var,
        node_voltages=node_voltages,
        predicates=predicates,
        boundaries=boundaries,
        interval=interval,
        binding=binding,
    )
