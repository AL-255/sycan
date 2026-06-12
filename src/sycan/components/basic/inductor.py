"""Inductor (SPICE ``L``).

Behaviour depends on the analysis mode:

* **DC**: ideal short. Stamped as a 0 V voltage source so ``V(n+) = V(n-)``.
  Contributes one auxiliary branch-current unknown ``I(name)``.
* **AC**: auxiliary-row formulation ``V(n+) - V(n-) - s·L·I = 0``.
  Keeps the branch current as an MNA variable so that mutual coupling
  (``K`` elements) can add ``-s·M`` off-diagonal terms.
* **Transient**: same as AC plus the initial-condition term from
  ``V(s) = L·(s·I(s) − i0)``, i.e. ``−L·i0`` on the KVL row RHS.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Optional

from sycan import cas as cas

from sycan.mna import Component, NoiseSpec, StampContext


@dataclass
class Inductor(Component):
    """Linear inductor; ``value`` is the inductance.

    ``ic`` is the optional initial current for transient analysis,
    positive flowing from ``n_plus`` to ``n_minus`` through the
    inductor (same direction as the branch unknown ``I(name)``).
    Solver-time ``initial_conditions`` overrides win over this field.

    Ideal inductors are noiseless; the ``include_noise`` parameter is
    accepted for interface uniformity but contributes no source.
    """

    name: str
    n_plus: str
    n_minus: str
    value: cas.Expr
    include_noise: NoiseSpec = field(default=None, kw_only=True)
    ic: Optional[cas.Expr] = field(default=None, kw_only=True)

    ports: ClassVar[tuple[str, ...]] = ("n_plus", "n_minus")
    has_aux: ClassVar[bool] = True
    SUPPORTED_NOISE: ClassVar[frozenset[str]] = frozenset()

    def __post_init__(self) -> None:
        self.value = cas.sympify(self.value)
        self.include_noise = self._normalize_noise(self.include_noise)
        if self.ic is not None:
            self.ic = cas.sympify(self.ic)

    def stamp(self, ctx: StampContext) -> None:
        aux = ctx.aux(self.name)
        i, j = ctx.n(self.n_plus), ctx.n(self.n_minus)

        # KCL: inductor current flows p → n (enters at p, leaves at n).
        if i >= 0:
            ctx.A[i, aux] += 1
            ctx.A[aux, i] += 1
        if j >= 0:
            ctx.A[j, aux] -= 1
            ctx.A[aux, j] -= 1

        # KVL: V(p) - V(n) = 0 (DC) or V(p) - V(n) - s·L·I = 0 (AC/tran).
        if ctx.mode in ("ac", "tran"):
            ctx.A[aux, aux] -= ctx.s * self.value
        if ctx.mode == "tran":
            i0 = ctx.ic(self.name)
            if i0 is not None:
                # V(s) = L·(s·I − i0) ⇒ V(p) − V(n) − sL·I = −L·i0.
                ctx.b[aux] -= self.value * i0
