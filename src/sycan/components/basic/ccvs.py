"""Current-controlled voltage source (SPICE ``H``)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from sycan import cas as cas

from sycan.mna import Component, NoiseSpec, StampContext


@dataclass
class CCVS(Component):
    """CCVS: ``V(n_plus) - V(n_minus) = gain * I(ctrl)``.

    ``ctrl`` is the name of the controlling voltage source; ``gain`` is
    a transresistance. SPICE form ``Hxxx N+ N- VNAM GAIN``. Ideal
    controlled sources are noiseless; ``include_noise`` is accepted for
    interface uniformity.
    """

    name: str
    n_plus: str
    n_minus: str
    ctrl: str
    gain: cas.Expr
    include_noise: NoiseSpec = field(default=None, kw_only=True)

    ports: ClassVar[tuple[str, ...]] = ("n_plus", "n_minus")
    has_aux: ClassVar[bool] = True
    SUPPORTED_NOISE: ClassVar[frozenset[str]] = frozenset()

    def __post_init__(self) -> None:
        self.gain = cas.sympify(self.gain)
        self.include_noise = self._normalize_noise(self.include_noise)

    def stamp(self, ctx: StampContext) -> None:
        aux = ctx.aux(self.name)
        ctrl_aux = ctx.aux(self.ctrl)
        i, j = ctx.n(self.n_plus), ctx.n(self.n_minus)
        if i >= 0:
            ctx.A[i, aux] += 1
            ctx.A[aux, i] += 1
        if j >= 0:
            ctx.A[j, aux] -= 1
            ctx.A[aux, j] -= 1
        ctx.A[aux, ctrl_aux] -= self.gain
