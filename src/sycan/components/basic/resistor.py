"""Resistor (SPICE ``R``)."""
from __future__ import annotations

from dataclasses import dataclass

import sympy as sp

from sycan.mna import Component, StampContext


@dataclass
class Resistor(Component):
    """Linear resistor; ``value`` is the resistance."""

    name: str
    n_plus: str
    n_minus: str
    value: sp.Expr

    def __post_init__(self) -> None:
        self.value = sp.sympify(self.value)

    def stamp(self, ctx: StampContext) -> None:
        i, j = ctx.n(self.n_plus), ctx.n(self.n_minus)
        g = sp.Integer(1) / self.value
        if i >= 0:
            ctx.A[i, i] += g
        if j >= 0:
            ctx.A[j, j] += g
        if i >= 0 and j >= 0:
            ctx.A[i, j] -= g
            ctx.A[j, i] -= g
