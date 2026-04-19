"""Impedance-analysis mode: apply a small ``dv`` at a named ``Port`` and
measure ``di`` to back out ``Z = dv/di``. Other ports are terminated per
the ``termination`` rule (open for Z-parameters, short for Y-parameters,
or role-based for ``"auto"``).
"""
import sympy as sp
import pytest

from sycan import parse, solve_impedance


def test_port_sees_series_R_shunt_C():
    """Z = R + 1/(sC) for a resistor in series with a capacitor to ground."""
    netlist = """series RC from a port
P1 in 0 input
R1 in mid R
C1 mid 0 C
.end
"""
    R, C, s = sp.symbols("R C s")
    Z = solve_impedance(parse(netlist), "P1")
    assert sp.simplify(sp.together(Z - (R + 1 / (s * C)))) == 0


def test_port_sees_two_parallel_resistors():
    """Z = R1 || R2 seen from a port."""
    netlist = """parallel R
P1 in 0 input
R1 in 0 R1
R2 in 0 R2
.end
"""
    R1, R2 = sp.symbols("R1 R2")
    Z = solve_impedance(parse(netlist), "P1")
    expected = R1 * R2 / (R1 + R2)
    assert sp.simplify(Z - expected) == 0


def test_auto_termination_leaves_output_open():
    """In a 2-port pi-network, auto-termination at the input leaves the
    output port open, so R1 sees an open circuit at its far end and
    contributes nothing; Z_in = R2."""
    netlist = """2-port L-R
P_in  in  0 input
P_out out 0 output
R1 in out R1
R2 in 0   R2
.end
"""
    R1, R2 = sp.symbols("R1 R2")
    Z_in = solve_impedance(parse(netlist), "P_in", termination="auto")
    assert sp.simplify(Z_in - R2) == 0


def test_y_parameter_termination_shorts_output():
    """Y-parameter convention: all other ports shorted. The 2-port below
    becomes R2 || R1 when P_out is shorted to ground."""
    netlist = """2-port pi
P_in  in  0 input
P_out out 0 output
R1 in out R1
R2 in 0   R2
R3 out 0  R3
.end
"""
    R1, R2, R3 = sp.symbols("R1 R2 R3")
    Z_in = solve_impedance(parse(netlist), "P_in", termination="y")
    # Short at P_out kills R3 (replaced by wire), and R1 sees ground
    # through the short => Z_in = R2 || R1.
    expected = R1 * R2 / (R1 + R2)
    assert sp.simplify(Z_in - expected) == 0


def test_z_parameter_termination_opens_output():
    """Z-parameter convention: all other ports open. Input impedance of
    the same pi-network with P_out open is R2 || (R1 + R3)."""
    netlist = """2-port pi
P_in  in  0 input
P_out out 0 output
R1 in out R1
R2 in 0   R2
R3 out 0  R3
.end
"""
    R1, R2, R3 = sp.symbols("R1 R2 R3")
    Z_in = solve_impedance(parse(netlist), "P_in", termination="z")
    expected = R2 * (R1 + R3) / (R1 + R2 + R3)
    assert sp.simplify(Z_in - expected) == 0
