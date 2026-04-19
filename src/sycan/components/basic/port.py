"""Port marker (single-ended or differential).

A ``Port`` is a label, not a circuit element: it records a name,
a pair of nodes ``(n_plus, n_minus)`` and an optional ``role``
(``"input"``, ``"output"`` or ``"generic"``). It contributes nothing
to the MNA stamps; the :func:`~sycan.solve_impedance` analysis uses
it to pick an injection point and to auto-terminate the other ports
(input ports default to short, output ports to open).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from sycan.mna import Component, StampContext


@dataclass
class Port(Component):
    name: str
    n_plus: str
    n_minus: str = "0"
    role: str = "generic"  # "input", "output", or "generic"

    has_aux: ClassVar[bool] = False

    def __post_init__(self) -> None:
        self.role = self.role.lower()
        if self.role not in ("input", "output", "generic"):
            raise ValueError(
                f"Port {self.name!r}: role must be 'input', 'output' or "
                f"'generic', got {self.role!r}"
            )

    def stamp(self, ctx: StampContext) -> None:
        return None
