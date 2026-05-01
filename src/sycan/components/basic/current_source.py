"""Independent current source (SPICE ``I``)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Optional

from sycan import cas as cas

from sycan.mna import Component, NoiseSpec, StampContext


@dataclass
class CurrentSource(Component):
    """Ideal current source.

    Drives current ``value`` from ``n_plus`` to ``n_minus`` internally,
    so externally the source pulls current out of ``n_plus`` and injects
    it into ``n_minus`` (SPICE convention).

    ``ac_value`` is the small-signal phasor used in AC analysis; if
    ``None``, AC analysis reuses ``value``. Ideal sources are noiseless;
    ``include_noise`` is accepted for interface uniformity.
    """

    name: str
    n_plus: str
    n_minus: str
    value: cas.Expr
    ac_value: Optional[cas.Expr] = None
    include_noise: NoiseSpec = field(default=None, kw_only=True)

    ports: ClassVar[tuple[str, ...]] = ("n_plus", "n_minus")
    SUPPORTED_NOISE: ClassVar[frozenset[str]] = frozenset()

    def __post_init__(self) -> None:
        self.value = cas.sympify(self.value)
        if self.ac_value is not None:
            self.ac_value = cas.sympify(self.ac_value)
        self.include_noise = self._normalize_noise(self.include_noise)

    def stamp(self, ctx: StampContext) -> None:
        i, j = ctx.n(self.n_plus), ctx.n(self.n_minus)
        val = (
            self.ac_value
            if ctx.mode == "ac" and self.ac_value is not None
            else self.value
        )
        if i >= 0:
            ctx.b[i] -= val
        if j >= 0:
            ctx.b[j] += val
