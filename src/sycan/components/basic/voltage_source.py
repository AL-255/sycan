"""Independent voltage source (SPICE ``V``)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Optional

from sycan import cas as cas

from sycan.mna import Component, NoiseSpec, StampContext


@dataclass
class VoltageSource(Component):
    """Ideal voltage source enforcing ``V(n_plus) - V(n_minus) = value``.

    Its auxiliary current ``I(name)`` is defined from ``n_plus`` to
    ``n_minus`` through the source, matching SPICE's convention.

    ``ac_value`` is the small-signal phasor used in AC analysis. If
    ``None``, AC analysis reuses the DC ``value``. Ideal sources are
    noiseless; ``include_noise`` is accepted for interface uniformity.
    """

    name: str
    n_plus: str
    n_minus: str
    value: cas.Expr
    ac_value: Optional[cas.Expr] = None
    include_noise: NoiseSpec = field(default=None, kw_only=True)

    ports: ClassVar[tuple[str, ...]] = ("n_plus", "n_minus")
    has_aux: ClassVar[bool] = True
    SUPPORTED_NOISE: ClassVar[frozenset[str]] = frozenset()

    def __post_init__(self) -> None:
        self.value = cas.sympify(self.value)
        if self.ac_value is not None:
            self.ac_value = cas.sympify(self.ac_value)
        self.include_noise = self._normalize_noise(self.include_noise)

    def stamp(self, ctx: StampContext) -> None:
        aux = ctx.aux(self.name)
        i, j = ctx.n(self.n_plus), ctx.n(self.n_minus)
        if i >= 0:
            ctx.A[i, aux] += 1
            ctx.A[aux, i] += 1
        if j >= 0:
            ctx.A[j, aux] -= 1
            ctx.A[aux, j] -= 1
        val = (
            self.ac_value
            if ctx.mode == "ac" and self.ac_value is not None
            else self.value
        )
        ctx.b[aux] = val
