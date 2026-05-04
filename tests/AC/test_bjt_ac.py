"""BJT AC small-signal hybrid-pi model tests."""
from sycan import cas as cas

from sycan import Circuit, solve_ac


def test_bjt_ac_no_caps():
    """BJT AC model — hybrid-pi transconductance produces gain."""
    I_C, R_C = cas.symbols("I_C R_C", positive=True)
    c = Circuit("ce_no_caps")
    c.add_vsource("Vcc", "vcc", "0", value=cas.Symbol("V_CC"))
    c.add_resistor("RC", "vcc", "out", R_C)
    c.add_bjt("Q1", "out", "in", "0", "NPN",
              IS=1e-15, BF=100, BR=1,
              I_C_op=I_C, I_B_op=I_C/100)
    c.add_vsource("Vsig", "sig", "0", value=0, ac_value=1)
    c.add_resistor("Rsig", "sig", "in", cas.Symbol("Rsig"))
    c.add_vsource("Vbb", "bb", "0", value=cas.Symbol("V_BB"))
    c.add_resistor("Rbb", "bb", "in", cas.Symbol("Rbb"))

    sol = solve_ac(c)
    Vout = sol[cas.Symbol("V(out)")]
    assert Vout is not None
    assert I_C in Vout.free_symbols


def test_bjt_ac_with_caps():
    """BJT AC with C_pi and C_mu capacitances."""
    I_C, R_C = cas.symbols("I_C R_C", positive=True)
    s = cas.Symbol("s")
    c = Circuit("ce_caps")
    c.add_vsource("Vcc", "vcc", "0", value=cas.Symbol("V_CC"))
    c.add_resistor("RC", "vcc", "out", R_C)
    c.add_bjt("Q1", "out", "in", "0", "NPN",
              IS=1e-15, BF=100, BR=1,
              I_C_op=I_C, I_B_op=I_C/100,
              C_pi=cas.Symbol("C_pi"), C_mu=cas.Symbol("C_mu"))
    c.add_vsource("Vsig", "sig", "0", value=0, ac_value=1)
    c.add_resistor("Rsig", "sig", "in", cas.Symbol("Rsig"))
    c.add_vsource("Vbb", "bb", "0", value=cas.Symbol("V_BB"))
    c.add_resistor("Rbb", "bb", "in", cas.Symbol("Rbb"))

    sol = solve_ac(c)
    Vout = sol[cas.Symbol("V(out)")]
    assert Vout is not None
    assert s in Vout.free_symbols


def test_bjt_pnp_ac():
    """PNP BJT also gets an AC model."""
    c = Circuit("pnp_ac")
    c.add_vsource("Vcc", "vcc", "0", -5)
    c.add_resistor("RC", "out", "vcc", cas.Symbol("R_C"))
    c.add_bjt("Q1", "out", "in", "0", "PNP",
              IS=1e-15, BF=50, BR=1,
              I_C_op=cas.Symbol("I_C"))
    c.add_vsource("Vsig", "sig", "0", value=0, ac_value=1)
    c.add_resistor("Rsig", "sig", "in", cas.Symbol("Rsig"))
    c.add_vsource("Vbb", "bb", "0", value=cas.Symbol("V_BB"))
    c.add_resistor("Rbb", "bb", "in", cas.Symbol("Rbb"))
    sol = solve_ac(c)
    assert cas.Symbol("V(out)") in sol
