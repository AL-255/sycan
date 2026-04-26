"""Analytical lossless transmission line.

A two-port device parameterised by characteristic impedance ``Z0`` and
one-way time delay ``td``. The small-signal ABCD matrix in the Laplace
domain is the classical

    [V1]   [cosh(s td)        Z0 sinh(s td)] [ V2]
    [I1] = [sinh(s td) / Z0    cosh(s td)  ] [-I2]

which corresponds to the Y-parameter matrix

    Y = (1 / Z0) * [[ coth(s td),  -csch(s td) ],
                    [-csch(s td),   coth(s td) ]]

Stamping strategy:

* **AC**: write each admittance contribution straight into the MNA
  matrix at the four port nodes (single-ended is the special case
  ``n_in_m = n_out_m = "0"``).
* **DC**: the inner conductor is a pure wire, so we stamp a 0 V source
  between ``n_in_p`` and ``n_out_p`` exactly like the ``W`` primitive.
  Outer conductors are assumed to share a node (usually ground); if
  both are ground the short is harmless, otherwise a DC solve will
  treat them as separately floating — that case currently isn't
  modelled at DC.

Only the lossless case (real ``Z0``, no attenuation) is covered here;
a lossy version would replace ``s td`` with ``γ l = (α + s/v) l``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

import sympy as sp

from sycan.mna import Component, NoiseSpec, StampContext


@dataclass
class TLINE(Component):
    """Lossless 2-port transmission line.

    Lossless transmission lines are noiseless; ``include_noise`` is
    accepted for interface uniformity.
    """

    name: str
    n_in_p: str
    n_in_m: str
    n_out_p: str
    n_out_m: str
    Z0: sp.Expr
    td: sp.Expr
    include_noise: NoiseSpec = field(default=None, kw_only=True)

    ports: ClassVar[tuple[str, ...]] = ("n_in_p", "n_in_m", "n_out_p", "n_out_m")
    SUPPORTED_NOISE: ClassVar[frozenset[str]] = frozenset()

    def __post_init__(self) -> None:
        self.Z0 = sp.sympify(self.Z0)
        self.td = sp.sympify(self.td)
        self.include_noise = self._normalize_noise(self.include_noise)

    # DC: short the inner conductor with an auxiliary branch current.
    def aux_count(self, mode: str) -> int:
        return 1 if mode == "dc" else 0

    # ------------------------------------------------------------------

    def _stamp_dc(self, ctx: StampContext) -> None:
        aux = ctx.aux(self.name)
        i, j = ctx.n(self.n_in_p), ctx.n(self.n_out_p)
        if i >= 0:
            ctx.A[i, aux] += 1
            ctx.A[aux, i] += 1
        if j >= 0:
            ctx.A[j, aux] -= 1
            ctx.A[aux, j] -= 1
        # b[aux] = 0 -> V(n_in_p) = V(n_out_p).

    def _stamp_ac(self, ctx: StampContext) -> None:
        s = ctx.s
        theta = s * self.td
        Y_self = sp.cosh(theta) / (self.Z0 * sp.sinh(theta))   # coth(sτ)/Z0
        Y_mut = -1 / (self.Z0 * sp.sinh(theta))                # -csch(sτ)/Z0

        p1 = ctx.n(self.n_in_p)
        m1 = ctx.n(self.n_in_m)
        p2 = ctx.n(self.n_out_p)
        m2 = ctx.n(self.n_out_m)

        def add(row: int, col: int, val: sp.Expr) -> None:
            if row >= 0 and col >= 0:
                ctx.A[row, col] += val

        # Port 1 KCL rows  -> i1  = Y_self (V_p1 - V_m1) + Y_mut (V_p2 - V_m2)
        for row, sign in ((p1, +1), (m1, -1)):
            if row < 0:
                continue
            add(row, p1, sign * Y_self)
            add(row, m1, -sign * Y_self)
            add(row, p2, sign * Y_mut)
            add(row, m2, -sign * Y_mut)

        # Port 2 KCL rows  -> i2  = Y_mut (V_p1 - V_m1) + Y_self (V_p2 - V_m2)
        for row, sign in ((p2, +1), (m2, -1)):
            if row < 0:
                continue
            add(row, p1, sign * Y_mut)
            add(row, m1, -sign * Y_mut)
            add(row, p2, sign * Y_self)
            add(row, m2, -sign * Y_self)

    def stamp(self, ctx: StampContext) -> None:
        if ctx.mode == "dc":
            self._stamp_dc(ctx)
        elif ctx.mode == "ac":
            self._stamp_ac(ctx)
