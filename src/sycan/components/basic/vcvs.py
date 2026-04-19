"""Voltage-controlled voltage source (SPICE ``E``)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

import sympy as sp

from sycan.mna import Component, StampContext


@dataclass
class VCVS(Component):
    """VCVS: ``V(n_plus) - V(n_minus) = gain * (V(nc_plus) - V(nc_minus))``.

    SPICE form ``Exxx N+ N- NC+ NC- GAIN``.
    """

    name: str
    n_plus: str
    n_minus: str
    nc_plus: str
    nc_minus: str
    gain: sp.Expr

    has_aux: ClassVar[bool] = True

    def __post_init__(self) -> None:
        self.gain = sp.sympify(self.gain)

    def stamp(self, ctx: StampContext) -> None:
        aux = ctx.aux(self.name)
        i, j = ctx.n(self.n_plus), ctx.n(self.n_minus)
        ci, cj = ctx.n(self.nc_plus), ctx.n(self.nc_minus)
        if i >= 0:
            ctx.A[i, aux] += 1
            ctx.A[aux, i] += 1
        if j >= 0:
            ctx.A[j, aux] -= 1
            ctx.A[aux, j] -= 1
        if ci >= 0:
            ctx.A[aux, ci] -= self.gain
        if cj >= 0:
            ctx.A[aux, cj] += self.gain
