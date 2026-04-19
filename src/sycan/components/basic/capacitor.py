"""Capacitor (SPICE ``C``).

* **DC**: ideal open, i.e. not stamped.
* **AC**: admittance ``sC``.
"""
from __future__ import annotations

from dataclasses import dataclass

import sympy as sp

from sycan.mna import Component, StampContext


@dataclass
class Capacitor(Component):
    """Linear capacitor; ``value`` is the capacitance."""

    name: str
    n_plus: str
    n_minus: str
    value: sp.Expr

    def __post_init__(self) -> None:
        self.value = sp.sympify(self.value)

    def stamp(self, ctx: StampContext) -> None:
        if ctx.mode == "dc":
            return

        Y = ctx.s * self.value
        i, j = ctx.n(self.n_plus), ctx.n(self.n_minus)
        if i >= 0:
            ctx.A[i, i] += Y
        if j >= 0:
            ctx.A[j, j] += Y
        if i >= 0 and j >= 0:
            ctx.A[i, j] -= Y
            ctx.A[j, i] -= Y
