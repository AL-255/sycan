"""MNA infrastructure: component base, stamping context, and solvers.

Two analysis modes are supported:

* ``"dc"`` — steady-state operating point. Inductors short, capacitors open.
* ``"ac"`` — small-signal frequency-domain analysis using a Laplace
  variable ``s``. Capacitors stamp admittance ``sC``; inductors stamp
  ``1/(sL)``. Source AC values override DC values.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar, Optional, Union

import sympy as sp

if TYPE_CHECKING:
    from sycan.circuit import Circuit

Value = Union[sp.Expr, int, float, str]


@dataclass
class StampContext:
    """Mutable state handed to each component's ``stamp`` method."""

    A: sp.Matrix
    b: sp.Matrix
    node_rows: dict[str, int]
    aux_rows: dict[str, int]
    mode: str = "dc"  # "dc" or "ac"
    s: Optional[sp.Expr] = None  # Laplace variable, set in AC mode

    def n(self, node: str) -> int:
        """MNA row of a node name; ``-1`` for ground."""
        try:
            return self.node_rows[node]
        except KeyError:
            raise ValueError(f"unknown node {node!r}") from None

    def aux(self, name: str) -> int:
        """MNA row of a component's auxiliary branch current."""
        try:
            return self.aux_rows[name]
        except KeyError:
            raise ValueError(
                f"component {name!r} has no auxiliary branch current "
                "(not a V/E/H/L-in-DC source?)"
            ) from None


class Component(ABC):
    """Netlist element that can stamp itself into an MNA matrix.

    Subclasses that introduce a branch-current unknown set
    ``has_aux = True``; the builder then allocates a row/column
    labelled ``I(name)`` for each such element. Components whose
    auxiliary requirement depends on the analysis mode (e.g. the
    inductor, which is a short in DC but an admittance in AC) override
    :meth:`aux_count` directly.
    """

    name: str
    has_aux: ClassVar[bool] = False

    def aux_count(self, mode: str) -> int:
        """Number of auxiliary branch currents the component needs in ``mode``."""
        return int(self.has_aux)

    @abstractmethod
    def stamp(self, ctx: StampContext) -> None: ...


def build_mna(
    circuit: "Circuit",
    mode: str = "dc",
    s: Optional[sp.Expr] = None,
) -> tuple[sp.Matrix, sp.Matrix, sp.Matrix]:
    """Assemble the symbolic MNA system ``A * x = b`` for ``mode``.

    ``x`` contains node voltages first (in ``circuit.nodes`` order),
    then one auxiliary branch current per component whose
    :meth:`Component.aux_count` is non-zero for this mode.
    """
    if mode == "ac" and s is None:
        s = sp.Symbol("s")

    nodes = circuit.nodes
    n = len(nodes)
    aux_owners = [c for c in circuit.components if c.aux_count(mode) > 0]
    m = len(aux_owners)

    A = sp.zeros(n + m, n + m)
    b = sp.zeros(n + m, 1)
    node_rows = {name: idx - 1 for name, idx in circuit._nodes.items()}
    aux_rows = {c.name: n + k for k, c in enumerate(aux_owners)}

    ctx = StampContext(
        A=A, b=b, node_rows=node_rows, aux_rows=aux_rows, mode=mode, s=s
    )
    for c in circuit.components:
        c.stamp(ctx)

    x = sp.Matrix(
        [sp.Symbol(f"V({nm})") for nm in nodes]
        + [sp.Symbol(f"I({c.name})") for c in aux_owners]
    )
    return A, x, b


def solve_dc(circuit: "Circuit", simplify: bool = True) -> dict[sp.Symbol, sp.Expr]:
    """Solve the DC operating point symbolically."""
    A, x, b = build_mna(circuit, mode="dc")
    sol = A.LUsolve(b)
    result = {sym: expr for sym, expr in zip(x, sol)}
    if simplify:
        result = {sym: sp.simplify(expr) for sym, expr in result.items()}
    return result


def solve_ac(
    circuit: "Circuit",
    s: Optional[sp.Expr] = None,
    simplify: bool = False,
) -> dict[sp.Symbol, sp.Expr]:
    """Solve the small-signal AC response in the Laplace domain.

    ``s`` is the complex frequency variable; if omitted, a free symbol
    ``s`` is used. Returns a mapping from each unknown symbol to its
    complex, frequency-dependent expression. Simplification is off by
    default because rational functions in ``s`` can be expensive to
    simplify.
    """
    if s is None:
        s = sp.Symbol("s")
    A, x, b = build_mna(circuit, mode="ac", s=s)
    sol = A.LUsolve(b)
    result = {sym: expr for sym, expr in zip(x, sol)}
    if simplify:
        result = {sym: sp.simplify(expr) for sym, expr in result.items()}
    return result
