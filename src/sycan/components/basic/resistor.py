"""Resistor (SPICE ``R``)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from sycan import cas as cas

from sycan.mna import Component, NoiseSource, NoiseSpec, StampContext, T, k_B


@dataclass
class Resistor(Component):
    """Linear resistor; ``value`` is the resistance.

    Pass ``include_noise="thermal"`` (or ``"all"``) to enable a
    Johnson-Nyquist current noise source with one-sided PSD
    ``4·k_B·T / R`` between ``n_plus`` and ``n_minus``.
    """

    name: str
    n_plus: str
    n_minus: str
    value: cas.Expr
    include_noise: NoiseSpec = field(default=None, kw_only=True)

    ports: ClassVar[tuple[str, ...]] = ("n_plus", "n_minus")
    SUPPORTED_NOISE: ClassVar[frozenset[str]] = frozenset({"thermal"})

    def __post_init__(self) -> None:
        self.value = cas.sympify(self.value)
        self.include_noise = self._normalize_noise(self.include_noise)

    def stamp(self, ctx: StampContext) -> None:
        i, j = ctx.n(self.n_plus), ctx.n(self.n_minus)
        g = cas.Integer(1) / self.value
        if i >= 0:
            ctx.A[i, i] += g
        if j >= 0:
            ctx.A[j, j] += g
        if i >= 0 and j >= 0:
            ctx.A[i, j] -= g
            ctx.A[j, i] -= g

    def noise_sources(self) -> list[NoiseSource]:
        out: list[NoiseSource] = []
        if "thermal" in self.include_noise:
            out.append(
                NoiseSource(
                    name=f"{self.name}.thermal",
                    kind="thermal",
                    n_plus=self.n_plus,
                    n_minus=self.n_minus,
                    psd=4 * k_B * T / self.value,
                )
            )
        return out
