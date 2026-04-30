"""Continuous-time integrator block.

Implements ``H(s) = k / (s + leak)`` — ideal pure integrator when
``leak = 0``, lossy / leaky integrator otherwise. The latter is useful
when an exact pure integrator would make the DC operating point
indeterminate (the ideal integrator has an infinite DC gain, so this
block leaves its output floating in DC mode).

The auxiliary stamping is identical to a frequency-dependent VCVS
controlled by the differential input.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

import sympy as sp

from sycan.mna import Component, NoiseSpec, StampContext


@dataclass
class Integrator(Component):
    name: str
    in_p: str
    in_m: str
    out_p: str
    out_m: str
    k: sp.Expr = sp.Integer(1)
    leak: sp.Expr = sp.Integer(0)
    var: sp.Symbol = field(default_factory=lambda: sp.Symbol("s"))
    include_noise: NoiseSpec = field(default=None, kw_only=True)

    ports: ClassVar[tuple[str, ...]] = ("in_p", "in_m", "out_p", "out_m")
    has_aux: ClassVar[bool] = True
    SUPPORTED_NOISE: ClassVar[frozenset[str]] = frozenset()

    def __post_init__(self) -> None:
        self.k = sp.sympify(self.k)
        self.leak = sp.sympify(self.leak)
        self.var = sp.sympify(self.var)
        self.include_noise = self._normalize_noise(self.include_noise)

    def aux_count(self, mode: str) -> int:
        if mode == "ac":
            return 1
        # Ideal integrator: open-circuit at DC. Leaky: stamps k / leak.
        return 0 if self.leak == 0 else 1

    def stamp(self, ctx: StampContext) -> None:
        if ctx.mode == "ac":
            gain = self.k / (ctx.s + self.leak)
        else:
            if self.leak == 0:
                return
            gain = self.k / self.leak

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
            ctx.A[aux, ci] -= gain
        if cj >= 0:
            ctx.A[aux, cj] += gain
