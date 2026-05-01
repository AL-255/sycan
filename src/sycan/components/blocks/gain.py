"""Static linear gain block: ``V_out = k * V_in``.

Equivalent to a unit-gain VCVS controlled by the differential input,
but with input/output port names ordered conventionally for signal-
flow diagrams.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from sycan import cas as cas

from sycan.mna import Component, NoiseSpec, StampContext


@dataclass
class Gain(Component):
    name: str
    in_p: str
    in_m: str
    out_p: str
    out_m: str
    k: cas.Expr
    include_noise: NoiseSpec = field(default=None, kw_only=True)

    ports: ClassVar[tuple[str, ...]] = ("in_p", "in_m", "out_p", "out_m")
    has_aux: ClassVar[bool] = True
    SUPPORTED_NOISE: ClassVar[frozenset[str]] = frozenset()

    def __post_init__(self) -> None:
        self.k = cas.sympify(self.k)
        self.include_noise = self._normalize_noise(self.include_noise)

    def stamp(self, ctx: StampContext) -> None:
        aux = ctx.aux(self.name)
        i, j = ctx.n(self.out_p), ctx.n(self.out_m)
        ci, cj = ctx.n(self.in_p), ctx.n(self.in_m)
        if i >= 0:
            ctx.A[i, aux] += 1
            ctx.A[aux, i] += 1
        if j >= 0:
            ctx.A[j, aux] -= 1
            ctx.A[aux, j] -= 1
        if ci >= 0:
            ctx.A[aux, ci] -= self.k
        if cj >= 0:
            ctx.A[aux, cj] += self.k
