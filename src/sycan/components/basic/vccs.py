"""Voltage-controlled current source (SPICE ``G``)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

import sympy as sp

from sycan.mna import Component, NoiseSpec, StampContext


@dataclass
class VCCS(Component):
    """VCCS: drives ``gain * (V(nc_plus) - V(nc_minus))`` from n+ to n-.

    SPICE form ``Gxxx N+ N- NC+ NC- GAIN``; ``gain`` is a transconductance.
    Ideal controlled sources are noiseless; ``include_noise`` is accepted
    for interface uniformity.
    """

    name: str
    n_plus: str
    n_minus: str
    nc_plus: str
    nc_minus: str
    gain: sp.Expr
    include_noise: NoiseSpec = field(default=None, kw_only=True)

    ports: ClassVar[tuple[str, ...]] = ("n_plus", "n_minus", "nc_plus", "nc_minus")
    SUPPORTED_NOISE: ClassVar[frozenset[str]] = frozenset()

    def __post_init__(self) -> None:
        self.gain = sp.sympify(self.gain)
        self.include_noise = self._normalize_noise(self.include_noise)

    def stamp(self, ctx: StampContext) -> None:
        i, j = ctx.n(self.n_plus), ctx.n(self.n_minus)
        ci, cj = ctx.n(self.nc_plus), ctx.n(self.nc_minus)
        g = self.gain
        if i >= 0:
            if ci >= 0:
                ctx.A[i, ci] += g
            if cj >= 0:
                ctx.A[i, cj] -= g
        if j >= 0:
            if ci >= 0:
                ctx.A[j, ci] -= g
            if cj >= 0:
                ctx.A[j, cj] += g
