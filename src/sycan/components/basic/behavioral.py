"""Behavioral sources (SPICE ``B`` elements).

Two flavours, both letting the user pin an arbitrary symbolic expression
to a pair of nodes:

* :class:`BehavioralCurrent` — drives the current ``I = f(V(...), …)``
  from ``n_plus`` to ``n_minus``.  Equivalent to SPICE
  ``B1 N+ N- I=...`` and ngspice's ``Vxxx ... value={...}`` form.
* :class:`BehavioralVoltage` — enforces the voltage
  ``V(n_plus) - V(n_minus) = f(V(...), …)``.  Equivalent to SPICE
  ``B1 N+ N- V=...``.

The expression may reference any sympy ``Symbol``.  Node voltages are
addressed using the same naming convention as the MNA solution vector:
``Symbol("V(<node>)")``.  A behavioural element therefore captures
nonlinear control laws — squarers, multipliers, sign extractors,
saturating amplifiers — without having to wire them up from primitives.

* **DC** — the expression is used directly via :meth:`stamp_nonlinear`,
  so even non-polynomial control laws (``tanh``, ``Min``, ``Max``,
  exponentials, etc.) flow through the existing damped-Newton path.
* **AC** — the expression is linearised around an operating point
  ``V_op_subs`` (a mapping ``{Symbol: value}``).  Partial derivatives
  with respect to every referenced node voltage are stamped as VCCS /
  VCVS contributions.  If ``V_op_subs`` is ``None``, the operating
  point is left symbolic — fine for symbolic transfer-function work.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Mapping, Optional

from sycan import cas as cas

from sycan.mna import Component, NoiseSpec, StampContext


def _node_symbols(expr: cas.Expr) -> list[cas.Symbol]:
    """Return all ``V(<node>)`` symbols appearing in ``expr``."""
    out: list[cas.Symbol] = []
    for sym in sorted(expr.free_symbols, key=lambda s: str(s)):
        name = str(sym)
        if name.startswith("V(") and name.endswith(")"):
            out.append(sym)
    return out


def _substitute_op(expr: cas.Expr, op: Optional[Mapping]) -> cas.Expr:
    if not op:
        return expr
    return expr.subs({cas.sympify(k): cas.sympify(v) for k, v in op.items()})


@dataclass
class BehavioralCurrent(Component):
    """Current source whose value is an arbitrary symbolic expression.

    DC: residual contribution adds ``+expr`` to ``n_plus`` and
    ``-expr`` at ``n_minus`` (current flowing from + to - internally).

    AC: ``expr`` is linearised around ``V_op_subs``; each partial
    derivative ``∂expr/∂V(k)`` is stamped as a VCCS term from node ``k``
    to the (n+, n-) pair.  Constant terms in the linearisation are
    dropped — they belong to the DC operating point, not the small
    signal.
    """

    name: str
    n_plus: str
    n_minus: str
    expr: cas.Expr
    V_op_subs: Optional[dict] = field(default=None, kw_only=True)
    include_noise: NoiseSpec = field(default=None, kw_only=True)

    ports: ClassVar[tuple[str, ...]] = ("n_plus", "n_minus")
    has_nonlinear: ClassVar[bool] = True
    SUPPORTED_NOISE: ClassVar[frozenset[str]] = frozenset()

    def __post_init__(self) -> None:
        self.expr = cas.sympify(self.expr)
        self.include_noise = self._normalize_noise(self.include_noise)

    def stamp(self, ctx: StampContext) -> None:
        if ctx.mode not in ("ac", "tran"):
            return
        i, j = ctx.n(self.n_plus), ctx.n(self.n_minus)
        for v_sym in _node_symbols(self.expr):
            gm = cas.diff(self.expr, v_sym)
            gm = _substitute_op(gm, self.V_op_subs)
            if gm == 0:
                continue
            node = str(v_sym)[2:-1]
            try:
                k = ctx.n(node)
            except ValueError:
                continue  # symbol references an unregistered node
            if i >= 0 and k >= 0:
                ctx.A[i, k] += gm
            if j >= 0 and k >= 0:
                ctx.A[j, k] -= gm

    def stamp_nonlinear(self, ctx: StampContext) -> None:
        if ctx.mode != "dc":
            return
        assert ctx.x is not None and ctx.residuals is not None

        # Map V(node) symbols in the user expression onto the actual
        # MNA unknowns ctx.x[node_row].
        subs = {}
        for v_sym in _node_symbols(self.expr):
            node = str(v_sym)[2:-1]
            try:
                idx = ctx.n(node)
            except ValueError:
                continue
            subs[v_sym] = ctx.x[idx] if idx >= 0 else cas.Integer(0)
        I_b = self.expr.subs(subs) if subs else self.expr

        i, j = ctx.n(self.n_plus), ctx.n(self.n_minus)
        if i >= 0:
            ctx.residuals[i] += I_b
        if j >= 0:
            ctx.residuals[j] -= I_b


@dataclass
class BehavioralVoltage(Component):
    """Voltage source whose value is an arbitrary symbolic expression.

    Enforces ``V(n_plus) - V(n_minus) = expr``. Behaves like a normal
    voltage source for stamping (introduces an aux branch current),
    but with a non-constant right-hand side.

    DC: the constraint row directly gets ``-expr`` from the node
    unknowns.  Linear references resolve in the linear path; nonlinear
    expressions go through the nonlinear residual.

    AC: linearised around ``V_op_subs`` — only first-order terms remain.
    """

    name: str
    n_plus: str
    n_minus: str
    expr: cas.Expr
    V_op_subs: Optional[dict] = field(default=None, kw_only=True)
    include_noise: NoiseSpec = field(default=None, kw_only=True)

    ports: ClassVar[tuple[str, ...]] = ("n_plus", "n_minus")
    has_aux: ClassVar[bool] = True
    has_nonlinear: ClassVar[bool] = True
    SUPPORTED_NOISE: ClassVar[frozenset[str]] = frozenset()

    def __post_init__(self) -> None:
        self.expr = cas.sympify(self.expr)
        self.include_noise = self._normalize_noise(self.include_noise)

    def stamp(self, ctx: StampContext) -> None:
        aux = ctx.aux(self.name)
        i, j = ctx.n(self.n_plus), ctx.n(self.n_minus)
        if i >= 0:
            ctx.A[i, aux] += 1
            ctx.A[aux, i] += 1
        if j >= 0:
            ctx.A[j, aux] -= 1
            ctx.A[aux, j] -= 1

        if ctx.mode in ("ac", "tran"):
            # Linearise: V(+) - V(-) = sum_k (d expr / d V(k))_op · v(k).
            # The aux row is V(+) - V(-) - sum_k ... = 0 (constant op-point
            # term contributes only to the DC bias, which has been zeroed in
            # the small-signal system).
            for v_sym in _node_symbols(self.expr):
                gm = cas.diff(self.expr, v_sym)
                gm = _substitute_op(gm, self.V_op_subs)
                if gm == 0:
                    continue
                node = str(v_sym)[2:-1]
                try:
                    k = ctx.n(node)
                except ValueError:
                    continue
                if k >= 0:
                    ctx.A[aux, k] -= gm
            return

        # DC: rhs is the expression evaluated at node unknowns.
        # Linear chunks resolve symbolically inside MNA; nonlinear
        # chunks contribute through stamp_nonlinear.
        # Leave b[aux] = 0 here; the residual pass writes
        # V(+) - V(-) - expr(unknowns) into the aux residual row.
        ctx.b[aux] = 0

    def stamp_nonlinear(self, ctx: StampContext) -> None:
        if ctx.mode != "dc":
            return
        assert ctx.x is not None and ctx.residuals is not None

        aux = ctx.aux(self.name)
        subs = {}
        for v_sym in _node_symbols(self.expr):
            node = str(v_sym)[2:-1]
            try:
                idx = ctx.n(node)
            except ValueError:
                continue
            subs[v_sym] = ctx.x[idx] if idx >= 0 else cas.Integer(0)
        rhs = self.expr.subs(subs) if subs else self.expr
        # Aux row currently encodes V(+) - V(-) = 0; add `-rhs` so it
        # becomes V(+) - V(-) - rhs = 0.
        ctx.residuals[aux] -= rhs
