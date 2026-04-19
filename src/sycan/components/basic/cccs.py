"""Current-controlled current source (SPICE ``F``)."""
from __future__ import annotations

from dataclasses import dataclass

import sympy as sp

from sycan.mna import Component, StampContext


@dataclass
class CCCS(Component):
    """CCCS: drives ``gain * I(ctrl)`` from n+ to n-.

    ``ctrl`` is the name of a voltage source whose branch current is the
    controlling variable. SPICE form ``Fxxx N+ N- VNAM GAIN``.
    """

    name: str
    n_plus: str
    n_minus: str
    ctrl: str
    gain: sp.Expr

    def __post_init__(self) -> None:
        self.gain = sp.sympify(self.gain)

    def stamp(self, ctx: StampContext) -> None:
        i, j = ctx.n(self.n_plus), ctx.n(self.n_minus)
        ctrl_aux = ctx.aux(self.ctrl)
        if i >= 0:
            ctx.A[i, ctrl_aux] += self.gain
        if j >= 0:
            ctx.A[j, ctrl_aux] -= self.gain
