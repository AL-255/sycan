"""Capacitor (SPICE ``C``).

* **DC**: ideal open, i.e. not stamped.
* **AC**: admittance ``sC``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

import sympy as sp

from sycan.mna import Component, NoiseSpec, StampContext


@dataclass
class Capacitor(Component):
    """Linear capacitor; ``value`` is the capacitance.

    Ideal capacitors are noiseless; ``include_noise`` is accepted for
    interface uniformity but only ``None`` / ``"all"`` (which expands
    to the empty set) are valid.
    """

    name: str
    n_plus: str
    n_minus: str
    value: sp.Expr
    include_noise: NoiseSpec = field(default=None, kw_only=True)

    ports: ClassVar[tuple[str, ...]] = ("n_plus", "n_minus")
    SUPPORTED_NOISE: ClassVar[frozenset[str]] = frozenset()

    def __post_init__(self) -> None:
        self.value = sp.sympify(self.value)
        self.include_noise = self._normalize_noise(self.include_noise)

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
