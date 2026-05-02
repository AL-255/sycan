"""Ideal differential op-amp packaged as a :class:`SubCircuit`.

The body is the smallest faithful op-amp model — a single VCVS that
forces ``V(out) - V(0) = A * (V(in_p) - V(in_n))``. Wrapping it as a
subcircuit means hierarchical designs can drop ``OPAMP`` instances
into a netlist (and into the SPICE parser via ``Xxxx ... OPAMP``)
without rebuilding the underlying VCVS each time, and lets
:meth:`Circuit.print_hierarchy` show them as named blocks.

Higher-fidelity op-amp models (finite output impedance, dominant pole,
slew limit) can be added later as additional ``SubCircuit`` subclasses
without disturbing the existing single-VCVS topology.
"""
from __future__ import annotations

from typing import Optional

from sycan import cas as cas

from sycan.components.blocks.subcircuit import SubCircuit


class OPAMP(SubCircuit):
    """Ideal single-VCVS op-amp.

    Parameters
    ----------
    name
        Instance designator (e.g. ``"X1"``).
    in_p, in_n, out
        Parent-scope nodes wired to the non-inverting input, inverting
        input, and output pin respectively.
    A
        Open-loop voltage gain. Defaults to a per-instance symbol
        ``A_<name>`` so that ``A -> oo`` limits recover the ideal
        closed-loop behaviour.
    """

    def __init__(
        self,
        name: str,
        in_p: str,
        in_n: str,
        out: str,
        A: Optional[cas.Expr] = None,
    ) -> None:
        from sycan.circuit import Circuit

        if A is None:
            A = cas.Symbol(f"A_{name}")
        else:
            A = cas.sympify(A)

        body = Circuit(name="OPAMP")
        body.add_vcvs("E1", "out", "0", "in_p", "in_n", A)

        super().__init__(
            name=name,
            body=body,
            port_map={"in_p": in_p, "in_n": in_n, "out": out},
        )
        self.A = A
