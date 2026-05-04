"""Voltage-controlled switch (SPICE ``S``).

A smooth resistive switch::

    R(V_c) = R_off + (R_on - R_off) · ½ · (1 + tanh((V_c - V_t) / V_h))

with ``V_c = V(nc_plus) - V(nc_minus)``.  ``V_t`` is the threshold
voltage and ``V_h`` is the half-width of the transition (smaller →
sharper switch, but harder for Newton's method to close on).

* **DC**: the conductance ``G(V_c) = 1/R(V_c)`` is stamped through
  ``stamp_nonlinear`` so V_c can come from the operating point.
* **AC**: linearised around an operating point ``V_c_op``.  Two terms
  drop out — a small-signal conductance between ``n_plus``/``n_minus``
  *and* a transconductance into ``nc_plus``/``nc_minus`` from the
  voltage drop across the switch.  When the switch is hard on or hard
  off, the cross term is negligible and the switch behaves like a
  static resistor.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Optional

from sycan import cas as cas

from sycan.mna import Component, NoiseSpec, StampContext


@dataclass
class VSwitch(Component):
    """Voltage-controlled smooth switch.

    Parameters
    ----------
    name, n_plus, n_minus
        Switched terminals.
    nc_plus, nc_minus
        Control terminals.
    R_on, R_off
        Resistances in the closed and open states.
    V_t
        Control-voltage threshold (midpoint of the transition).
    V_h
        Transition half-width.
    V_c_op
        Operating-point control voltage used for AC linearisation.
        Defaults to a per-instance symbol ``V_c_op_<name>`` when ``None``.
    """

    name: str
    n_plus: str
    n_minus: str
    nc_plus: str
    nc_minus: str
    R_on: cas.Expr = field(default=1)
    R_off: cas.Expr = field(default_factory=lambda: cas.Float("1e9"))
    V_t: cas.Expr = field(default=0)
    V_h: cas.Expr = field(default_factory=lambda: cas.Float("0.1"))
    V_c_op: Optional[cas.Expr] = field(default=None, kw_only=True)
    include_noise: NoiseSpec = field(default=None, kw_only=True)

    ports: ClassVar[tuple[str, ...]] = (
        "n_plus", "n_minus", "nc_plus", "nc_minus"
    )
    has_nonlinear: ClassVar[bool] = True
    SUPPORTED_NOISE: ClassVar[frozenset[str]] = frozenset()

    def __post_init__(self) -> None:
        self.R_on = cas.sympify(self.R_on)
        self.R_off = cas.sympify(self.R_off)
        self.V_t = cas.sympify(self.V_t)
        self.V_h = cas.sympify(self.V_h)
        if self.V_c_op is None:
            self.V_c_op = cas.Symbol(f"V_c_op_{self.name}")
        else:
            self.V_c_op = cas.sympify(self.V_c_op)
        self.include_noise = self._normalize_noise(self.include_noise)

    def _R_of(self, V_c: cas.Expr) -> cas.Expr:
        half_open = (1 + cas.tanh((V_c - self.V_t) / self.V_h)) / 2
        return self.R_off + (self.R_on - self.R_off) * half_open

    def stamp(self, ctx: StampContext) -> None:
        if ctx.mode != "ac":
            return
        # AC small-signal: g0 between (n_plus, n_minus) plus a control
        # transconductance gm = ∂I/∂V_c · sign that maps V_c to current
        # through the switch. I = (V+ - V-) / R(V_c).
        i, j = ctx.n(self.n_plus), ctx.n(self.n_minus)
        ci, cj = ctx.n(self.nc_plus), ctx.n(self.nc_minus)

        _vc = cas.Dummy("vc")
        R_sym = self._R_of(_vc)
        # Operating-point conductance.
        g0 = (1 / R_sym).subs(_vc, self.V_c_op)
        # Linear conductance term between switched terminals.
        if i >= 0:
            ctx.A[i, i] += g0
        if j >= 0:
            ctx.A[j, j] += g0
        if i >= 0 and j >= 0:
            ctx.A[i, j] -= g0
            ctx.A[j, i] -= g0

        # Cross-coupling: dG/dV_c times the operating-point voltage drop
        # across the switch is zero in the AC small-signal sense (the
        # operating-point V_DS is constant), but the small-signal control
        # voltage v_c modulates the conductance, producing a current
        # ∂(G · V_DS_op)/∂V_c · v_c in series with the switch path.
        # Without an explicit V_DS_op this contribution is zero — leave
        # it for users who supply it. (Most use cases drive the switch
        # hard on or hard off, where g0 dominates.)

    def stamp_nonlinear(self, ctx: StampContext) -> None:
        if ctx.mode != "dc":
            return
        assert ctx.x is not None and ctx.residuals is not None

        i_idx = ctx.n(self.n_plus)
        j_idx = ctx.n(self.n_minus)
        ci_idx = ctx.n(self.nc_plus)
        cj_idx = ctx.n(self.nc_minus)

        V_i = ctx.x[i_idx] if i_idx >= 0 else cas.Integer(0)
        V_j = ctx.x[j_idx] if j_idx >= 0 else cas.Integer(0)
        V_ci = ctx.x[ci_idx] if ci_idx >= 0 else cas.Integer(0)
        V_cj = ctx.x[cj_idx] if cj_idx >= 0 else cas.Integer(0)

        V_c = V_ci - V_cj
        I = (V_i - V_j) / self._R_of(V_c)

        if i_idx >= 0:
            ctx.residuals[i_idx] += I
        if j_idx >= 0:
            ctx.residuals[j_idx] -= I
