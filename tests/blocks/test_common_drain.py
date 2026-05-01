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
from sycan import cas as cas

from sycan import parse, solve_ac, solve_dc, solve_impedance

_DC = """\
CD amp DC test
Vg g 0 V_G
Vs s 0 V_S
Vd d 0 V_D
M1 d g s NMOS_L1 mu_n Cox W L V_TH lam
.end
"""

_AC_GAIN = """\
CD amp AC gain test
Vin g 0 AC v_in
Vdd vdd 0 DC VDD
RL s 0 R_L
M1 vdd g s NMOS_L1 mu_n Cox W L V_TH lam V_GS_op V_DS_op
.end
"""

_ZIN = """\
CD amp Zin test
P_in g 0 input
P_out s 0 output
Vdd vdd 0 DC VDD
RL s 0 R_L
M1 vdd g s NMOS_L1 mu_n Cox W L V_TH lam V_GS_op V_DS_op 0 C_gd
.end
"""

_ZOUT = """\
CD amp Zout test
P_in g 0 input
P_out s 0 output
Vdd vdd 0 DC VDD
RL s 0 R_L
M1 vdd g s NMOS_L1 mu_n Cox W L V_TH lam V_GS_op V_DS_op
.end
"""


def _g_m_g_ds(mu_n, Cox, W, L, V_TH, lam, V_GS_op, V_DS_op):
    g_m = mu_n * Cox * (W / L) * (V_GS_op - V_TH) * (1 + lam * V_DS_op)
    g_ds = cas.Rational(1, 2) * mu_n * Cox * (W / L) * (V_GS_op - V_TH) ** 2 * lam
    return g_m, g_ds


# ---------------------------------------------------------------------------

def test_cd_dc_drain_current():
    V_G, V_S, V_D, mu_n, Cox, W, L, V_TH, lam = cas.symbols(
        "V_G V_S V_D mu_n Cox W L V_TH lam"
    )
    sol = solve_dc(parse(_DC))

    V_GS = V_G - V_S
    V_DS = V_D - V_S
    I_D = cas.Rational(1, 2) * mu_n * Cox * (W / L) * (V_GS - V_TH) ** 2 * (1 + lam * V_DS)
    assert cas.simplify(sol[cas.Symbol("I(Vd)")] + I_D) == 0


def test_cd_ac_voltage_gain():
    mu_n, Cox, W, L, V_TH, lam, R_L, VDD, V_GS_op, V_DS_op, v_in = cas.symbols(
        "mu_n Cox W L V_TH lam R_L VDD V_GS_op V_DS_op v_in"
    )
    sol = solve_ac(parse(_AC_GAIN))

    g_m, g_ds = _g_m_g_ds(mu_n, Cox, W, L, V_TH, lam, V_GS_op, V_DS_op)
    expected = g_m * v_in * R_L / (1 + (g_m + g_ds) * R_L)
    assert cas.simplify(sol[cas.Symbol("V(s)")] - expected) == 0


def test_cd_input_impedance():
    mu_n, Cox, W, L, V_TH, lam, R_L, VDD, V_GS_op, V_DS_op, C_gd = cas.symbols(
        "mu_n Cox W L V_TH lam R_L VDD V_GS_op V_DS_op C_gd"
    )
    Z_in = solve_impedance(parse(_ZIN), "P_in", termination="auto")
    s = cas.Symbol("s")
    expected = 1 / (s * C_gd)
    assert cas.simplify(Z_in - expected) == 0


def test_cd_output_impedance():
    mu_n, Cox, W, L, V_TH, lam, R_L, VDD, V_GS_op, V_DS_op = cas.symbols(
        "mu_n Cox W L V_TH lam R_L VDD V_GS_op V_DS_op"
    )
    Z_out = solve_impedance(parse(_ZOUT), "P_out", termination="auto")
    g_m, g_ds = _g_m_g_ds(mu_n, Cox, W, L, V_TH, lam, V_GS_op, V_DS_op)
    expected = R_L / (1 + (g_m + g_ds) * R_L)
    assert cas.simplify(cas.together(Z_out - expected)) == 0
