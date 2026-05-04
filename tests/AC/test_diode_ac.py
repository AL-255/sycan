"""Diode AC small-signal model tests."""
from sycan import cas as cas

from sycan import Circuit, solve_ac


def test_diode_ac_conductance():
    """Diode g_d in series with resistor forms a voltage divider."""
    IS, R, V_D_op = cas.symbols("IS R V_D_op", positive=True)
    c = Circuit("diode_ac_test")
    c.add_vsource("Vtest", "in", "0", 0, ac_value=1)
    c.add_resistor("R1", "in", "k", R)
    c.add_diode("D1", "k", "0", IS, V_D_op=V_D_op)
    sol = solve_ac(c)
    Vk = sol[cas.Symbol("V(k)")]
    assert V_D_op in Vk.free_symbols
    # g_d forms a divider with R: Vk = 1 * g_d_inv / (R + g_d_inv)
    # Not quite 1; the diode splits the voltage.


def test_diode_ac_capacitance():
    """Diode with C_j inserts s into the response."""
    IS, C_j, R, V_D_op = cas.symbols("IS C_j R V_D_op", positive=True)
    c = Circuit("diode_cap_test")
    c.add_vsource("Vac", "in", "0", 0, ac_value=1)
    c.add_resistor("R1", "in", "k", R)
    c.add_diode("D1", "k", "0", IS, C_j=C_j, V_D_op=V_D_op)
    sol = solve_ac(c)
    Vk = sol[cas.Symbol("V(k)")]
    s = cas.Symbol("s")
    assert s in Vk.free_symbols


def test_diode_ac_default_params():
    """Diode defaults produce valid AC model with autogen V_D_op."""
    c = Circuit("diode_defaults")
    c.add_vsource("Vac", "in", "0", 0, ac_value=1)
    c.add_resistor("R1", "in", "k", cas.Symbol("R"))
    c.add_diode("D1", "k", "0", cas.Symbol("IS"))
    sol = solve_ac(c)
    Vk = sol[cas.Symbol("V(k)")]
    assert Vk is not None


def test_diode_construction_with_new_params():
    """Diode accepts C_j and V_D_op without errors."""
    from sycan.components.active import Diode

    d = Diode("D9", "a", "k", 1e-12, C_j=1e-12, V_D_op=0.6)
    assert d.C_j == 1e-12
    assert d.V_D_op == 0.6
