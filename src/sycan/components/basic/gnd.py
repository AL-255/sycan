"""Ground reference (``GND``): pins a node to the absolute zero potential."""
from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from sycan.mna import Component, StampContext


@dataclass
class GND(Component):
    """Force ``V(node) = 0``.

    Functionally equivalent to a 0 V voltage source between ``node`` and
    the implicit ground (MNA node ``"0"``). Its branch current ``I(name)``
    reports whatever net current flows out of ``node`` into the ground
    reference.
    """

    name: str
    node: str

    has_aux: ClassVar[bool] = True

    def stamp(self, ctx: StampContext) -> None:
        aux = ctx.aux(self.name)
        i = ctx.n(self.node)
        # V(node) - V(0) = 0; only the non-ground side stamps.
        if i >= 0:
            ctx.A[i, aux] += 1
            ctx.A[aux, i] += 1
        # b[aux] stays 0.
