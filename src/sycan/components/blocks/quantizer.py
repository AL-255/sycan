"""Linear-model quantizer.

Implements the standard additive-noise model used in sigma-delta loop
analysis::

    V(out_p) - V(out_m) = k_q * (V(in_p) - V(in_m)) + V_q

``V_q`` is a sympy *symbol* (default ``V_q_<name>``) that appears
directly in the AC solution. With the loop closed around the
quantizer the user can read the signal-transfer-function (STF) off
the coefficient of ``V_in`` and the noise-transfer-function (NTF)
off the coefficient of ``V_q`` — no separate noise-PSD machinery
needed for this stage.

In DC mode the perturbation ``V_q`` is treated as zero and only the
linear ``k_q`` gain is stamped.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Optional

from sycan import cas as cas

from sycan.mna import Component, NoiseSpec, StampContext


@dataclass
class Quantizer(Component):
    name: str
    in_p: str
    in_m: str
    out_p: str
    out_m: str
    k_q: cas.Expr = cas.Integer(1)
    qnoise: Optional[cas.Expr] = None
    include_noise: NoiseSpec = field(default=None, kw_only=True)

    ports: ClassVar[tuple[str, ...]] = ("in_p", "in_m", "out_p", "out_m")
    has_aux: ClassVar[bool] = True
    SUPPORTED_NOISE: ClassVar[frozenset[str]] = frozenset()

    def __post_init__(self) -> None:
        self.k_q = cas.sympify(self.k_q)
        if self.qnoise is None:
            self.qnoise = cas.Symbol(f"V_q_{self.name}")
        else:
            self.qnoise = cas.sympify(self.qnoise)
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
            ctx.A[aux, ci] -= self.k_q
        if cj >= 0:
            ctx.A[aux, cj] += self.k_q
        if ctx.mode == "ac":
            ctx.b[aux] += self.qnoise
