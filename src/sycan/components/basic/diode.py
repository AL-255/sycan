"""Shockley diode DC model (SPICE ``D``).

Drain current through the diode, measured in the anode → cathode
direction::

    I_D = IS * (exp(V_D / (N V_T)) - 1)

with ``V_D = V(anode) - V(cathode)``, ``IS`` the reverse-saturation
current and ``N`` the ideality / emission coefficient (defaults to 1).

Like the BJT and NMOS models, the diode contributes a nonlinear KCL
term handled by ``stamp_nonlinear``; AC analysis is currently a no-op
(no small-signal linearisation yet).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

import sympy as sp

from sycan.mna import Component, StampContext

_DEFAULT_VT = sp.Rational(2585, 100000)


@dataclass
class Diode(Component):
    name: str
    anode: str
    cathode: str
    IS: sp.Expr
    N: sp.Expr = field(default_factory=lambda: sp.Integer(1))
    V_T: sp.Expr = field(default_factory=lambda: _DEFAULT_VT)

    has_nonlinear: ClassVar[bool] = True

    def __post_init__(self) -> None:
        self.IS = sp.sympify(self.IS)
        self.N = sp.sympify(self.N)
        self.V_T = sp.sympify(self.V_T)

    def stamp(self, ctx: StampContext) -> None:
        return None

    def stamp_nonlinear(self, ctx: StampContext) -> None:
        if ctx.mode != "dc":
            return
        assert ctx.x is not None and ctx.residuals is not None

        a_idx = ctx.n(self.anode)
        k_idx = ctx.n(self.cathode)

        V_a = ctx.x[a_idx] if a_idx >= 0 else sp.Integer(0)
        V_k = ctx.x[k_idx] if k_idx >= 0 else sp.Integer(0)
        V_D = V_a - V_k

        I_D = self.IS * (sp.exp(V_D / (self.N * self.V_T)) - 1)

        # I_D flows anode → cathode through the diode; externally it
        # leaves the anode node and enters the cathode node.
        if a_idx >= 0:
            ctx.residuals[a_idx] += I_D
        if k_idx >= 0:
            ctx.residuals[k_idx] -= I_D
