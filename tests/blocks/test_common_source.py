"""Common-source amplifier: DC bias, AC voltage gain, input impedance,
output impedance.

Topology::

        VDD
         |
         R_L
         |
         +---- drain = V_out
         |
        /
    [M1]    NMOS_L1, saturation
        \\
         |
     gate = V_in     source = GND

Closed-form expectations (for our Level-1 MOSFET and a load ``R_L``
to an AC-grounded supply):

* **DC**:  I_D = (1/2) mu_n Cox (W/L) (V_GS - V_TH)^2 (1 + lam V_DS)
* **AC gain**:  A_v = -g_m R_L / (1 + g_ds R_L)
* **Z_in**:     1 / (s C_gs)                    (C_gd = 0, no Miller)
* **Z_out**:    R_L / (1 + g_ds R_L)
"""
import sympy as sp

from sycan import Circuit, solve_ac, solve_dc, solve_impedance


def _sym(name):
    return sp.symbols(name)


def _g_m_g_ds(mu_n, Cox, W, L, V_TH, lam, V_GS_op, V_DS_op):
    g_m = mu_n * Cox * (W / L) * (V_GS_op - V_TH) * (1 + lam * V_DS_op)
    g_ds = sp.Rational(1, 2) * mu_n * Cox * (W / L) * (V_GS_op - V_TH) ** 2 * lam
    return g_m, g_ds


# ---------------------------------------------------------------------------

def test_cs_dc_drain_current():
    V_GS, V_DS, mu_n, Cox, W, L, V_TH, lam = sp.symbols(
        "V_GS V_DS mu_n Cox W L V_TH lam"
    )
    c = Circuit()
    c.add_vsource("Vgs", "gate",  "0", V_GS)
    c.add_vsource("Vds", "drain", "0", V_DS)
    c.add_nmos_l1("M1", "drain", "gate", "0",
                  mu_n=mu_n, Cox=Cox, W=W, L=L, V_TH=V_TH, lam=lam)
    sol = solve_dc(c)
    I_D = sp.Rational(1, 2) * mu_n * Cox * (W / L) * (V_GS - V_TH) ** 2 * (1 + lam * V_DS)
    assert sp.simplify(sol[sp.Symbol("I(Vds)")] + I_D) == 0


def test_cs_ac_voltage_gain():
    mu_n, Cox, W, L, V_TH, lam, R_L, VDD, V_GS_op, V_DS_op, v_in = sp.symbols(
        "mu_n Cox W L V_TH lam R_L VDD V_GS_op V_DS_op v_in"
    )
    c = Circuit()
    c.add_vsource("Vin", "gate", "0", value=0, ac_value=v_in)
    c.add_vsource("Vdd", "vdd",  "0", value=VDD, ac_value=0)
    c.add_resistor("RL", "vdd", "drain", R_L)
    c.add_nmos_l1("M1", "drain", "gate", "0",
                  mu_n=mu_n, Cox=Cox, W=W, L=L, V_TH=V_TH, lam=lam,
                  V_GS_op=V_GS_op, V_DS_op=V_DS_op)
    sol = solve_ac(c)

    g_m, g_ds = _g_m_g_ds(mu_n, Cox, W, L, V_TH, lam, V_GS_op, V_DS_op)
    expected = -g_m * v_in * R_L / (1 + g_ds * R_L)
    assert sp.simplify(sol[sp.Symbol("V(drain)")] - expected) == 0


def test_cs_input_impedance():
    mu_n, Cox, W, L, V_TH, lam, R_L, VDD, V_GS_op, V_DS_op, C_gs = sp.symbols(
        "mu_n Cox W L V_TH lam R_L VDD V_GS_op V_DS_op C_gs"
    )
    c = Circuit()
    c.add_port("P_in",  "gate",  "0", "input")
    c.add_port("P_out", "drain", "0", "output")
    c.add_vsource("Vdd", "vdd", "0", value=VDD, ac_value=0)
    c.add_resistor("RL", "vdd", "drain", R_L)
    c.add_nmos_l1("M1", "drain", "gate", "0",
                  mu_n=mu_n, Cox=Cox, W=W, L=L, V_TH=V_TH, lam=lam,
                  C_gs=C_gs,
                  V_GS_op=V_GS_op, V_DS_op=V_DS_op)

    Z_in = solve_impedance(c, "P_in", termination="auto")
    s = sp.Symbol("s")
    expected = 1 / (s * C_gs)
    assert sp.simplify(Z_in - expected) == 0


def test_cs_output_impedance():
    mu_n, Cox, W, L, V_TH, lam, R_L, VDD, V_GS_op, V_DS_op = sp.symbols(
        "mu_n Cox W L V_TH lam R_L VDD V_GS_op V_DS_op"
    )
    c = Circuit()
    c.add_port("P_in",  "gate",  "0", "input")
    c.add_port("P_out", "drain", "0", "output")
    c.add_vsource("Vdd", "vdd", "0", value=VDD, ac_value=0)
    c.add_resistor("RL", "vdd", "drain", R_L)
    c.add_nmos_l1("M1", "drain", "gate", "0",
                  mu_n=mu_n, Cox=Cox, W=W, L=L, V_TH=V_TH, lam=lam,
                  V_GS_op=V_GS_op, V_DS_op=V_DS_op)

    Z_out = solve_impedance(c, "P_out", termination="auto")
    _, g_ds = _g_m_g_ds(mu_n, Cox, W, L, V_TH, lam, V_GS_op, V_DS_op)
    expected = R_L / (1 + g_ds * R_L)
    assert sp.simplify(sp.together(Z_out - expected)) == 0
