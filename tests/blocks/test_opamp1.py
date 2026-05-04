"""Higher-fidelity op-amp (OPAMP1) tests."""
from sycan import cas as cas

from sycan import Circuit, solve_ac


def test_opamp1_non_inverting():
    """OPAMP1 non-inverting amplifier."""
    A, R1, R2 = cas.symbols("A R1 R2", positive=True)
    c = Circuit("noninv")
    c.add_opamp1("X1", "in_p", "in_n", "out", A=A)
    c.add_resistor("Rf", "out", "in_n", R2)
    c.add_resistor("Rg", "in_n", "0", R1)
    c.add_vsource("Vin", "in_p", "0", value=0, ac_value=1)
    sol = solve_ac(c)
    assert cas.Symbol("V(out)") in sol


def test_opamp1_with_gbw():
    """OPAMP1 with finite GBW introduces a pole dependent on s."""
    A, GBW, R1, R2 = cas.symbols("A GBW R1 R2", positive=True)
    s = cas.Symbol("s")
    c = Circuit("gbw_test")
    c.add_opamp1("X1", "in_p", "in_n", "out", A=A, GBW=GBW)
    c.add_resistor("Rf", "out", "in_n", R2)
    c.add_resistor("Rg", "in_n", "0", R1)
    c.add_vsource("Vin", "in_p", "0", value=0, ac_value=1)
    sol = solve_ac(c)
    Vout = sol[cas.Symbol("V(out)")]
    assert s in Vout.free_symbols and GBW in Vout.free_symbols


def test_opamp1_with_zout():
    """OPAMP1 with output impedance."""
    A, Z_out, RL = cas.symbols("A Z_out RL", positive=True)
    c = Circuit("zout_test")
    c.add_opamp1("X1", "in_p", "in_n", "out", A=A, Z_out=Z_out)
    c.add_resistor("RL", "out", "0", RL)
    c.add_vsource("Vin", "in_p", "0", value=0, ac_value=1)
    c.add_vsource("Vref", "in_n", "0", value=0, ac_value=0)
    sol = solve_ac(c)
    Vout = sol[cas.Symbol("V(out)")]
    assert Z_out in Vout.free_symbols


def test_opamp1_inverting():
    """OPAMP1 inverting amplifier with GBW and Z_out."""
    A, GBW, Z_out, R1, Rf = cas.symbols("A GBW Z_out R1 Rf", positive=True)
    c = Circuit("inv")
    c.add_opamp1("X1", "0", "in_n", "out", A=A, GBW=GBW, Z_out=Z_out)
    c.add_resistor("Rf", "out", "in_n", Rf)
    c.add_resistor("R1", "in_n", "in", R1)
    c.add_vsource("Vin", "in", "0", value=0, ac_value=1)
    sol = solve_ac(c)
    assert cas.Symbol("V(out)") in sol


def test_opamp1_construction():
    """OPAMP1 stores its parameters."""
    from sycan.components.blocks.opamp import OPAMP1

    op = OPAMP1("X9", "a", "b", "c", A=1e5, GBW=1e6, Z_out=75)
    assert op.A == 1e5
    assert op.GBW == 1e6
    assert op.Z_out == 75
