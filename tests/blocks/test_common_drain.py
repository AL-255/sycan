"""Common-drain (source-follower) amplifier: DC bias, AC voltage gain,
input impedance, output impedance.

Topology::

         VDD = drain
          |
         /
     [M1]     NMOS_L1, saturation
          \\
          |
          source = V_out
          |
          R_L
          |
          GND

     gate = V_in

Closed-form expectations:

* **DC**:    I_D = (1/2) mu_n Cox (W/L) (V_GS - V_TH)^2 (1 + lam V_DS)
* **AC**:    A_v = g_m R_L / (1 + (g_m + g_ds) R_L)      (approaches 1 for
                                                          large g_m R_L)
* **Z_in**:  1 / (s C_gd)                (C_gd from gate to AC-grounded
                                           drain gives a direct path)
* **Z_out**: R_L / (1 + (g_m + g_ds) R_L) (~ 1/g_m — the classic low
                                           source-follower output Z)
"""
import sympy as sp

from sycan import Circuit, solve_ac, solve_dc, solve_impedance


def _g_m_g_ds(mu_n, Cox, W, L, V_TH, lam, V_GS_op, V_DS_op):
    g_m = mu_n * Cox * (W / L) * (V_GS_op - V_TH) * (1 + lam * V_DS_op)
    g_ds = sp.Rational(1, 2) * mu_n * Cox * (W / L) * (V_GS_op - V_TH) ** 2 * lam
    return g_m, g_ds


# ---------------------------------------------------------------------------

def test_cd_dc_drain_current():
    V_G, V_S, V_D, mu_n, Cox, W, L, V_TH, lam = sp.symbols(
        "V_G V_S V_D mu_n Cox W L V_TH lam"
    )
    c = Circuit()
    c.add_vsource("Vg", "g", "0", V_G)
    c.add_vsource("Vs", "s", "0", V_S)
    c.add_vsource("Vd", "d", "0", V_D)
    c.add_nmos_l1("M1", "d", "g", "s",
                  mu_n=mu_n, Cox=Cox, W=W, L=L, V_TH=V_TH, lam=lam)
    sol = solve_dc(c)

    V_GS = V_G - V_S
    V_DS = V_D - V_S
    I_D = sp.Rational(1, 2) * mu_n * Cox * (W / L) * (V_GS - V_TH) ** 2 * (1 + lam * V_DS)
    assert sp.simplify(sol[sp.Symbol("I(Vd)")] + I_D) == 0


def test_cd_ac_voltage_gain():
    mu_n, Cox, W, L, V_TH, lam, R_L, VDD, V_GS_op, V_DS_op, v_in = sp.symbols(
        "mu_n Cox W L V_TH lam R_L VDD V_GS_op V_DS_op v_in"
    )
    c = Circuit()
    c.add_vsource("Vin", "g", "0", value=0, ac_value=v_in)
    c.add_vsource("Vdd", "vdd", "0", value=VDD, ac_value=0)
    c.add_resistor("RL", "s", "0", R_L)
    c.add_nmos_l1("M1", "vdd", "g", "s",
                  mu_n=mu_n, Cox=Cox, W=W, L=L, V_TH=V_TH, lam=lam,
                  V_GS_op=V_GS_op, V_DS_op=V_DS_op)
    sol = solve_ac(c)

    g_m, g_ds = _g_m_g_ds(mu_n, Cox, W, L, V_TH, lam, V_GS_op, V_DS_op)
    expected = g_m * v_in * R_L / (1 + (g_m + g_ds) * R_L)
    assert sp.simplify(sol[sp.Symbol("V(s)")] - expected) == 0


def test_cd_input_impedance():
    mu_n, Cox, W, L, V_TH, lam, R_L, VDD, V_GS_op, V_DS_op, C_gd = sp.symbols(
        "mu_n Cox W L V_TH lam R_L VDD V_GS_op V_DS_op C_gd"
    )
    c = Circuit()
    c.add_port("P_in",  "g", "0", "input")
    c.add_port("P_out", "s", "0", "output")
    c.add_vsource("Vdd", "vdd", "0", value=VDD, ac_value=0)
    c.add_resistor("RL", "s", "0", R_L)
    c.add_nmos_l1("M1", "vdd", "g", "s",
                  mu_n=mu_n, Cox=Cox, W=W, L=L, V_TH=V_TH, lam=lam,
                  C_gd=C_gd,
                  V_GS_op=V_GS_op, V_DS_op=V_DS_op)

    Z_in = solve_impedance(c, "P_in", termination="auto")
    s = sp.Symbol("s")
    expected = 1 / (s * C_gd)
    assert sp.simplify(Z_in - expected) == 0


def test_cd_output_impedance():
    mu_n, Cox, W, L, V_TH, lam, R_L, VDD, V_GS_op, V_DS_op = sp.symbols(
        "mu_n Cox W L V_TH lam R_L VDD V_GS_op V_DS_op"
    )
    c = Circuit()
    c.add_port("P_in",  "g", "0", "input")
    c.add_port("P_out", "s", "0", "output")
    c.add_vsource("Vdd", "vdd", "0", value=VDD, ac_value=0)
    c.add_resistor("RL", "s", "0", R_L)
    c.add_nmos_l1("M1", "vdd", "g", "s",
                  mu_n=mu_n, Cox=Cox, W=W, L=L, V_TH=V_TH, lam=lam,
                  V_GS_op=V_GS_op, V_DS_op=V_DS_op)

    Z_out = solve_impedance(c, "P_out", termination="auto")
    g_m, g_ds = _g_m_g_ds(mu_n, Cox, W, L, V_TH, lam, V_GS_op, V_DS_op)
    expected = R_L / (1 + (g_m + g_ds) * R_L)
    assert sp.simplify(sp.together(Z_out - expected)) == 0
