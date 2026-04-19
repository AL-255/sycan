"""Current-controlled voltage source (SPICE ``H``)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

import sympy as sp

from sycan.mna import Component, StampContext


@dataclass
class CCVS(Component):
    """CCVS: ``V(n_plus) - V(n_minus) = gain * I(ctrl)``.

    ``ctrl`` is the name of the controlling voltage source; ``gain`` is
    a transresistance. SPICE form ``Hxxx N+ N- VNAM GAIN``.
    """

    name: str
    n_plus: str
    n_minus: str
    ctrl: str
    gain: sp.Expr

    has_aux: ClassVar[bool] = True

    def __post_init__(self) -> None:
        self.gain = sp.sympify(self.gain)

    def stamp(self, ctx: StampContext) -> None:
        aux = ctx.aux(self.name)
        ctrl_aux = ctx.aux(self.ctrl)
        i, j = ctx.n(self.n_plus), ctx.n(self.n_minus)
        if i >= 0:
            ctx.A[i, aux] += 1
            ctx.A[aux, i] += 1
        if j >= 0:
            ctx.A[j, aux] -= 1
            ctx.A[aux, j] -= 1
        ctx.A[aux, ctrl_aux] -= self.gain
