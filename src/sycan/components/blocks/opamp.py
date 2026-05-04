"""Op-amp models packaged as :class:`SubCircuit` instances.

Two flavours are offered:

* :class:`OPAMP` — ideal single-VCVS (infinite bandwidth, zero output
  impedance).
* :class:`OPAMP1` — first-order model with finite gain-bandwidth
  product and optional output impedance.

Higher-fidelity op-amp models (slew limit, input offset, etc.) can be
added as additional ``SubCircuit`` subclasses.
"""
from __future__ import annotations

from typing import Optional

from sycan import cas as cas

from sycan.mna import Value
from sycan.components.blocks.subcircuit import SubCircuit


def _make_opamp_body(
    A: cas.Expr,
    GBW: Optional[cas.Expr] = None,
    Z_out: Optional[cas.Expr] = None,
) -> "Circuit":
    """Build the internal circuit for a first-order op-amp model.

    Parameters
    ----------
    A
        DC open-loop voltage gain.
    GBW
        Gain-bandwidth product (Hz). If ``None`` the op-amp is ideal
        (infinite bandwidth, a single VCVS with gain ``A``).
    Z_out
        Output impedance (Ω). If ``None`` or 0 the output is driven
        directly.
    """
    from sycan.circuit import Circuit

    body = Circuit(name="OPAMP")

    if GBW is not None and GBW != cas.oo:
        # First-order: H(s) = A * ω_p / (s + ω_p) with ω_p = 2π·GBW / A.
        # DC gain = A, unity-gain freq = GBW.
        var = cas.Symbol("s")
        omega_p = 2 * cas.pi * GBW / A
        H = A * omega_p / (var + omega_p)
        body.add_transfer_function("df", "in_p", "in_n", "out_int", "0",
                                  H=H, var=var)
    else:
        body.add_vcvs("E1", "out_int", "0", "in_p", "in_n", A)

    if Z_out is not None and Z_out != 0:
        body.add_resistor("Rout", "out_int", "out", Z_out)
    else:
        body.add_vcvs("Eout", "out", "0", "out_int", "0", 1)

    return body


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
        if A is None:
            A = cas.Symbol(f"A_{name}")
        else:
            A = cas.sympify(A)

        body = _make_opamp_body(A=A, GBW=None, Z_out=None)

        super().__init__(
            name=name,
            body=body,
            port_map={"in_p": in_p, "in_n": in_n, "out": out},
        )
        self.A = A


class OPAMP1(SubCircuit):
    """First-order op-amp with finite gain-bandwidth product and output Z.

    Builds a single-pole dominant-pole model::

        H(s) = A · ω_p / (s + ω_p)   with ω_p = 2π·GBW / A

    followed by an optional series output impedance ``Z_out``.

    Parameters
    ----------
    name
        Instance designator.
    in_p, in_n, out
        Parent-scope nodes.
    A
        DC open-loop gain (defaults to ``A_<name>``).
    GBW
        Gain-bandwidth product in Hz (default ``None`` = ideal,
        infinite bandwidth).
    Z_out
        Output impedance in Ω (default ``None`` = 0).
    """

    def __init__(
        self,
        name: str,
        in_p: str,
        in_n: str,
        out: str,
        A: Optional[Value] = None,
        GBW: Optional[Value] = None,
        Z_out: Optional[Value] = None,
    ) -> None:
        if A is None:
            A = cas.Symbol(f"A_{name}")
        else:
            A = cas.sympify(A)
        if GBW is not None:
            GBW = cas.sympify(GBW)
        if Z_out is not None:
            Z_out = cas.sympify(Z_out)

        body = _make_opamp_body(A=A, GBW=GBW, Z_out=Z_out)

        super().__init__(
            name=name,
            body=body,
            port_map={"in_p": in_p, "in_n": in_n, "out": out},
        )
        self.A = A
        self.GBW = GBW
        self.Z_out = Z_out
