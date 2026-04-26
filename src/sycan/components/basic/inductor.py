"""Inductor (SPICE ``L``).

Behaviour depends on the analysis mode:

* **DC**: ideal short. Stamped as a 0 V voltage source so ``V(n+) = V(n-)``.
  Contributes one auxiliary branch-current unknown ``I(name)``.
* **AC**: admittance ``1/(s L)``. No auxiliary unknown.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

import sympy as sp

from sycan.mna import Component, NoiseSpec, StampContext


@dataclass
class Inductor(Component):
    """Linear inductor; ``value`` is the inductance.

    Ideal inductors are noiseless; the ``include_noise`` parameter is
    accepted for interface uniformity but contributes no source.
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

    def aux_count(self, mode: str) -> int:
        return 1 if mode == "dc" else 0

    def stamp(self, ctx: StampContext) -> None:
        if ctx.mode == "dc":
            aux = ctx.aux(self.name)
            i, j = ctx.n(self.n_plus), ctx.n(self.n_minus)
            if i >= 0:
                ctx.A[i, aux] += 1
                ctx.A[aux, i] += 1
            if j >= 0:
                ctx.A[j, aux] -= 1
                ctx.A[aux, j] -= 1
            # b[aux] stays 0 (zero-volt short).
            return

        # AC: admittance Y = 1 / (s * L)
        Y = sp.Integer(1) / (ctx.s * self.value)
        i, j = ctx.n(self.n_plus), ctx.n(self.n_minus)
        if i >= 0:
            ctx.A[i, i] += Y
        if j >= 0:
            ctx.A[j, j] += Y
        if i >= 0 and j >= 0:
            ctx.A[i, j] -= Y
            ctx.A[j, i] -= Y
