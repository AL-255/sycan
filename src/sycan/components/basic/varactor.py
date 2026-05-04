"""Varactor — voltage-controlled capacitor (junction-style C(V) model).

* **DC**: ideal open (a capacitor sees zero current at steady state).
* **AC**: small-signal admittance ``s · C(V_op)`` where the operating-point
  voltage ``V_op`` linearises the bias-dependent capacitance::

      C(V) = C0 / (1 - V / V_J) ** M

  ``V`` is taken as ``V(n_plus) - V(n_minus)``. Setting ``V_op = 0``
  (default) recovers ``C0``. The form is the standard SPICE ``CJO``,
  ``VJ``, ``M`` junction-capacitance trio.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Optional

from sycan import cas as cas

from sycan.mna import Component, NoiseSpec, StampContext


@dataclass
class Varactor(Component):
    """Junction-style voltage-controlled capacitor.

    Parameters
    ----------
    name, n_plus, n_minus
        Designator and the two terminals.
    C0
        Zero-bias capacitance (``CJO``).
    V_J
        Junction potential (default ``0.7``).
    M
        Grading coefficient (default ``0.5``, abrupt junction).
    V_op
        Operating-point bias used to linearise ``C(V)`` for AC.
        Default ``0`` (no bias) — useful when the user later substitutes
        the DC operating point into the symbolic AC result.
    """

    name: str
    n_plus: str
    n_minus: str
    C0: cas.Expr
    V_J: cas.Expr = field(default=0.7)
    M: cas.Expr = field(default=0.5)
    V_op: Optional[cas.Expr] = field(default=None, kw_only=True)
    include_noise: NoiseSpec = field(default=None, kw_only=True)

    ports: ClassVar[tuple[str, ...]] = ("n_plus", "n_minus")
    SUPPORTED_NOISE: ClassVar[frozenset[str]] = frozenset()

    def __post_init__(self) -> None:
        self.C0 = cas.sympify(self.C0)
        self.V_J = cas.sympify(self.V_J)
        self.M = cas.sympify(self.M)
        if self.V_op is not None:
            self.V_op = cas.sympify(self.V_op)
        self.include_noise = self._normalize_noise(self.include_noise)

    def _C_small_signal(self) -> cas.Expr:
        if self.V_op is None or self.V_op == 0:
            return self.C0
        return self.C0 / (1 - self.V_op / self.V_J) ** self.M

    def stamp(self, ctx: StampContext) -> None:
        if ctx.mode == "dc":
            return

        Y = ctx.s * self._C_small_signal()
        i, j = ctx.n(self.n_plus), ctx.n(self.n_minus)
        if i >= 0:
            ctx.A[i, i] += Y
        if j >= 0:
            ctx.A[j, j] += Y
        if i >= 0 and j >= 0:
            ctx.A[i, j] -= Y
            ctx.A[j, i] -= Y
