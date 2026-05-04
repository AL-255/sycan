"""JFET AC small-signal model tests."""
from sycan import cas as cas

from sycan import Circuit, solve_ac


def test_njfet_ac_conductance():
    """NJFET gm and gds appear in AC transfer response."""
    BETA, R, V_GS_op, V_DS_op, VTO = cas.symbols(
        "BETA R V_GS_op V_DS_op VTO", positive=True
    )
    c = Circuit("njfet_ac_test")
    c.add_vsource("Vtest", "in", "0", 0, ac_value=1)
    c.add_resistor("R1", "in", "d", R)
    c.add_njfet("J1", "d", "in", "0", BETA, VTO, V_GS_op=V_GS_op, V_DS_op=V_DS_op)
    sol = solve_ac(c)
    Vd = sol[cas.Symbol("V(d)")]
    # V_GS_op appears in gm; V_DS_op appears via g_ds only when LAMBDA != 0
    assert V_GS_op in Vd.free_symbols
    # With LAMBDA=0 (default), g_ds=0 so V_DS_op may not be in free symbols
    # Add LAMBDA to verify it appears
    c2 = Circuit("njfet_ac_test2")
    c2.add_vsource("Vtest", "in", "0", 0, ac_value=1)
    c2.add_resistor("R1", "in", "d", R)
    LAMBDA = cas.Symbol("LAMBDA", positive=True)
    c2.add_njfet("J2", "d", "in", "0", BETA, VTO, LAMBDA=LAMBDA,
                 V_GS_op=V_GS_op, V_DS_op=V_DS_op)
    sol2 = solve_ac(c2)
    Vd2 = sol2[cas.Symbol("V(d)")]
    assert V_DS_op in Vd2.free_symbols


def test_pjfet_ac_conductance():
    """PJFET gm and gds appear in AC transfer."""
    BETA, R, V_GS_op, V_DS_op, VTO = cas.symbols(
        "BETA R V_GS_op V_DS_op VTO", positive=True
    )
    c = Circuit("pjfet_ac_test")
    c.add_vsource("Vtest", "in", "0", 0, ac_value=1)
    c.add_resistor("R1", "in", "d", R)
    c.add_pjfet("J1", "d", "in", "0", BETA, VTO, V_GS_op=V_GS_op, V_DS_op=V_DS_op)
    sol = solve_ac(c)
    Vd = sol[cas.Symbol("V(d)")]
    assert V_GS_op in Vd.free_symbols
    # With non-zero LAMBDA, V_DS_op appears via g_ds
    c2 = Circuit("pjfet_ac_test2")
    c2.add_vsource("Vtest", "in", "0", 0, ac_value=1)
    c2.add_resistor("R1", "in", "d", R)
    LAMBDA = cas.Symbol("LAMBDA", positive=True)
    c2.add_pjfet("J2", "d", "in", "0", BETA, VTO, LAMBDA=LAMBDA,
                 V_GS_op=V_GS_op, V_DS_op=V_DS_op)
    sol2 = solve_ac(c2)
    Vd2 = sol2[cas.Symbol("V(d)")]
    assert V_DS_op in Vd2.free_symbols


def test_njfet_ac_capacitance():
    """NJFET with C_gs/C_gd introduces s in the transfer function."""
    BETA, C_gs, C_gd, R, V_GS_op, V_DS_op, VTO = cas.symbols(
        "BETA C_gs C_gd R V_GS_op V_DS_op VTO", positive=True
    )
    c = Circuit("njfet_cap_test")
    c.add_vsource("Vac", "in", "0", 0, ac_value=1)
    c.add_resistor("R1", "in", "d", R)
    c.add_njfet(
        "J1", "d", "in", "0", BETA, VTO,
        C_gs=C_gs, C_gd=C_gd,
        V_GS_op=V_GS_op, V_DS_op=V_DS_op,
    )
    sol = solve_ac(c)
    Vd = sol[cas.Symbol("V(d)")]
    s = cas.Symbol("s")
    assert s in Vd.free_symbols


def test_pjfet_ac_capacitance():
    """PJFET with C_gs/C_gd introduces s in the transfer function."""
    BETA, C_gs, C_gd, R, V_GS_op, V_DS_op, VTO = cas.symbols(
        "BETA C_gs C_gd R V_GS_op V_DS_op VTO", positive=True
    )
    c = Circuit("pjfet_cap_test")
    c.add_vsource("Vac", "in", "0", 0, ac_value=1)
    c.add_resistor("R1", "in", "d", R)
    c.add_pjfet(
        "J1", "d", "in", "0", BETA, VTO,
        C_gs=C_gs, C_gd=C_gd,
        V_GS_op=V_GS_op, V_DS_op=V_DS_op,
    )
    sol = solve_ac(c)
    Vd = sol[cas.Symbol("V(d)")]
    s = cas.Symbol("s")
    assert s in Vd.free_symbols


def test_njfet_ac_default_params():
    """NJFET defaults produce valid AC model with autogen V_GS_op, V_DS_op."""
    c = Circuit("njfet_defaults")
    c.add_vsource("Vac", "in", "0", 0, ac_value=1)
    c.add_resistor("R1", "in", "d", cas.Symbol("R"))
    c.add_njfet("J1", "d", "in", "0", cas.Symbol("BETA"), cas.Symbol("VTO"))
    sol = solve_ac(c)
    Vd = sol[cas.Symbol("V(d)")]
    assert Vd is not None


def test_pjfet_ac_default_params():
    """PJFET defaults produce valid AC model with autogen V_GS_op, V_DS_op."""
    c = Circuit("pjfet_defaults")
    c.add_vsource("Vac", "in", "0", 0, ac_value=1)
    c.add_resistor("R1", "in", "d", cas.Symbol("R"))
    c.add_pjfet("J1", "d", "in", "0", cas.Symbol("BETA"), cas.Symbol("VTO"))
    sol = solve_ac(c)
    Vd = sol[cas.Symbol("V(d)")]
    assert Vd is not None
