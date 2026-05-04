"""Inductor (SPICE ``L``).

Behaviour depends on the analysis mode:

* **DC**: ideal short. Stamped as a 0 V voltage source so ``V(n+) = V(n-)``.
  Contributes one auxiliary branch-current unknown ``I(name)``.
* **AC**: auxiliary-row formulation ``V(n+) - V(n-) - s·L·I = 0``.
  Keeps the branch current as an MNA variable so that mutual coupling
  (``K`` elements) can add ``-s·M`` off-diagonal terms.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from sycan import cas as cas

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
    value: cas.Expr
    include_noise: NoiseSpec = field(default=None, kw_only=True)

    ports: ClassVar[tuple[str, ...]] = ("n_plus", "n_minus")
    has_aux: ClassVar[bool] = True
    SUPPORTED_NOISE: ClassVar[frozenset[str]] = frozenset()

    def __post_init__(self) -> None:
        self.value = cas.sympify(self.value)
        self.include_noise = self._normalize_noise(self.include_noise)

    def stamp(self, ctx: StampContext) -> None:
        aux = ctx.aux(self.name)
        i, j = ctx.n(self.n_plus), ctx.n(self.n_minus)

        # KCL: inductor current flows p → n (enters at p, leaves at n).
        if i >= 0:
            ctx.A[i, aux] += 1
            ctx.A[aux, i] += 1
        if j >= 0:
            ctx.A[j, aux] -= 1
            ctx.A[aux, j] -= 1

        # KVL: V(p) - V(n) = 0 (DC) or V(p) - V(n) - s·L·I = 0 (AC).
        if ctx.mode == "ac":
            ctx.A[aux, aux] -= ctx.s * self.value
