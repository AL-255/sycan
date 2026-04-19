"""Independent current source (SPICE ``I``)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import sympy as sp

from sycan.mna import Component, StampContext


@dataclass
class CurrentSource(Component):
    """Ideal current source.

    Drives current ``value`` from ``n_plus`` to ``n_minus`` internally,
    so externally the source pulls current out of ``n_plus`` and injects
    it into ``n_minus`` (SPICE convention).

    ``ac_value`` is the small-signal phasor used in AC analysis; if
    ``None``, AC analysis reuses ``value``.
    """

    name: str
    n_plus: str
    n_minus: str
    value: sp.Expr
    ac_value: Optional[sp.Expr] = None

    def __post_init__(self) -> None:
        self.value = sp.sympify(self.value)
        if self.ac_value is not None:
            self.ac_value = sp.sympify(self.ac_value)

    def stamp(self, ctx: StampContext) -> None:
        i, j = ctx.n(self.n_plus), ctx.n(self.n_minus)
        val = (
            self.ac_value
            if ctx.mode == "ac" and self.ac_value is not None
            else self.value
        )
        if i >= 0:
            ctx.b[i] -= val
        if j >= 0:
            ctx.b[j] += val
