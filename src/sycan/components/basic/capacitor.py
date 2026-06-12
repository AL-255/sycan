"""Capacitor (SPICE ``C``).

* **DC**: ideal open, i.e. not stamped.
* **AC**: admittance ``sC``.
* **Transient**: admittance ``sC`` plus an initial-condition current
  injection ``C·v0`` (from ``i_C(s) = sC·V(s) − C·v0``).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Optional

from sycan import cas as cas

from sycan.mna import Component, NoiseSpec, StampContext


@dataclass
class Capacitor(Component):
    """Linear capacitor; ``value`` is the capacitance.

    ``ic`` is the optional initial voltage for transient analysis,
    with polarity ``v0 = V(n_plus) − V(n_minus)`` at ``t = 0⁻``.
    Solver-time ``initial_conditions`` overrides win over this field.

    Ideal capacitors are noiseless; ``include_noise`` is accepted for
    interface uniformity but only ``None`` / ``"all"`` (which expands
    to the empty set) are valid.
    """

    name: str
    n_plus: str
    n_minus: str
    value: cas.Expr
    include_noise: NoiseSpec = field(default=None, kw_only=True)
    ic: Optional[cas.Expr] = field(default=None, kw_only=True)

    ports: ClassVar[tuple[str, ...]] = ("n_plus", "n_minus")
    SUPPORTED_NOISE: ClassVar[frozenset[str]] = frozenset()

    def __post_init__(self) -> None:
        self.value = cas.sympify(self.value)
        self.include_noise = self._normalize_noise(self.include_noise)
        if self.ic is not None:
            self.ic = cas.sympify(self.ic)

    def stamp(self, ctx: StampContext) -> None:
        if ctx.mode == "dc":
            return

        Y = ctx.s * self.value
        i, j = ctx.n(self.n_plus), ctx.n(self.n_minus)
        if i >= 0:
            ctx.A[i, i] += Y
        if j >= 0:
            ctx.A[j, j] += Y
        if i >= 0 and j >= 0:
            ctx.A[i, j] -= Y
            ctx.A[j, i] -= Y

        if ctx.mode == "tran":
            v0 = ctx.ic(self.name)
            if v0 is not None:
                # i_C(s) = sC·V(s) − C·v0: the constant C·v0 term moves
                # to the RHS as a current injection into n_plus.
                q0 = self.value * v0
                if i >= 0:
                    ctx.b[i] += q0
                if j >= 0:
                    ctx.b[j] -= q0
