"""Sub-threshold NMOS transistor (SPICE ``M``, type ``NMOS_subthreshold``).

Drain current in the sub-threshold (weak-inversion) regime::

    I_D = mu_n * Cox * (W/L) * V_T**2
          * exp((V_GS - m V_TH) / (m V_T))
          * (1 - exp(-V_DS / V_T))

        = mu_n * Cox * (W/L) * V_T**2
          * exp(V_GS / (m V_T)) * exp(-V_TH / V_T)
          * (1 - exp(-V_DS / V_T))

with ``V_GS = V(gate) - V(source)``, ``V_DS = V(drain) - V(source)``.
``m = 1 + C_d/C_ox`` is the sub-threshold slope factor and ``V_T`` is
the thermal voltage. The threshold enters the exponent at ``V_T`` scale
(``-V_TH/V_T``), which yields the classical 2-T reference expression in
which V_TH1 and V_TH2 appear symmetrically weighted.

Current flows drain → source through the transistor; the gate draws
zero current and bulk is tied to source (three-terminal model).

Only DC analysis is modelled. In AC the transistor is currently stamped
as zero (no small-signal model yet).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

import sympy as sp

from sycan.mna import Component, StampContext

# Thermal voltage kT/q at ~300 K, in volts.
_DEFAULT_VT = sp.Rational(2585, 100000)
# Typical sub-threshold slope factor for a long-channel MOSFET.
_DEFAULT_M = sp.Rational(3, 2)


@dataclass
class NMOS_subthreshold(Component):
    name: str
    drain: str
    gate: str
    source: str
    mu_n: sp.Expr  # electron mobility
    Cox: sp.Expr  # oxide capacitance per unit area
    W: sp.Expr  # channel width
    L: sp.Expr  # channel length
    V_TH: sp.Expr  # threshold voltage
    m: sp.Expr = field(default_factory=lambda: _DEFAULT_M)
    V_T: sp.Expr = field(default_factory=lambda: _DEFAULT_VT)

    has_nonlinear: ClassVar[bool] = True

    def __post_init__(self) -> None:
        self.mu_n = sp.sympify(self.mu_n)
        self.Cox = sp.sympify(self.Cox)
        self.W = sp.sympify(self.W)
        self.L = sp.sympify(self.L)
        self.V_TH = sp.sympify(self.V_TH)
        self.m = sp.sympify(self.m)
        self.V_T = sp.sympify(self.V_T)

    def stamp(self, ctx: StampContext) -> None:
        return None

    def stamp_nonlinear(self, ctx: StampContext) -> None:
        if ctx.mode != "dc":
            return
        assert ctx.x is not None and ctx.residuals is not None

        d_idx = ctx.n(self.drain)
        g_idx = ctx.n(self.gate)
        s_idx = ctx.n(self.source)

        V_d = ctx.x[d_idx] if d_idx >= 0 else sp.Integer(0)
        V_g = ctx.x[g_idx] if g_idx >= 0 else sp.Integer(0)
        V_s = ctx.x[s_idx] if s_idx >= 0 else sp.Integer(0)

        V_GS = V_g - V_s
        V_DS = V_d - V_s

        prefactor = self.mu_n * self.Cox * (self.W / self.L) * self.V_T**2
        I_D = (
            prefactor
            * sp.exp((V_GS - self.m * self.V_TH) / (self.m * self.V_T))
            * (1 - sp.exp(-V_DS / self.V_T))
        )

        if d_idx >= 0:
            ctx.residuals[d_idx] += I_D
        if s_idx >= 0:
            ctx.residuals[s_idx] -= I_D
