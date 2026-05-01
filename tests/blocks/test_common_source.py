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
from sycan import cas as cas

from sycan import parse, solve_ac, solve_dc, solve_impedance

_DC = """\
CS amp DC test
Vgs gate 0 V_GS
Vds drain 0 V_DS
M1 drain gate 0 NMOS_L1 mu_n Cox W L V_TH lam
.end
"""

_AC_GAIN = """\
CS amp AC gain test
Vin gate 0 AC v_in
Vdd vdd 0 DC VDD
RL vdd drain R_L
M1 drain gate 0 NMOS_L1 mu_n Cox W L V_TH lam V_GS_op V_DS_op
.end
"""

_ZIN = """\
CS amp Zin test
P_in gate 0 input
P_out drain 0 output
Vdd vdd 0 DC VDD
RL vdd drain R_L
M1 drain gate 0 NMOS_L1 mu_n Cox W L V_TH lam V_GS_op V_DS_op C_gs
.end
"""

_ZOUT = """\
CS amp Zout test
P_in gate 0 input
P_out drain 0 output
Vdd vdd 0 DC VDD
RL vdd drain R_L
M1 drain gate 0 NMOS_L1 mu_n Cox W L V_TH lam V_GS_op V_DS_op
.end
"""


def _g_m_g_ds(mu_n, Cox, W, L, V_TH, lam, V_GS_op, V_DS_op):
    g_m = mu_n * Cox * (W / L) * (V_GS_op - V_TH) * (1 + lam * V_DS_op)
    g_ds = cas.Rational(1, 2) * mu_n * Cox * (W / L) * (V_GS_op - V_TH) ** 2 * lam
    return g_m, g_ds


# ---------------------------------------------------------------------------

def test_cs_dc_drain_current():
    V_GS, V_DS, mu_n, Cox, W, L, V_TH, lam = cas.symbols(
        "V_GS V_DS mu_n Cox W L V_TH lam"
    )
    sol = solve_dc(parse(_DC))
    I_D = cas.Rational(1, 2) * mu_n * Cox * (W / L) * (V_GS - V_TH) ** 2 * (1 + lam * V_DS)
    assert cas.simplify(sol[cas.Symbol("I(Vds)")] + I_D) == 0


def test_cs_ac_voltage_gain():
    mu_n, Cox, W, L, V_TH, lam, R_L, VDD, V_GS_op, V_DS_op, v_in = cas.symbols(
        "mu_n Cox W L V_TH lam R_L VDD V_GS_op V_DS_op v_in"
    )
    sol = solve_ac(parse(_AC_GAIN))

    g_m, g_ds = _g_m_g_ds(mu_n, Cox, W, L, V_TH, lam, V_GS_op, V_DS_op)
    expected = -g_m * v_in * R_L / (1 + g_ds * R_L)
    assert cas.simplify(sol[cas.Symbol("V(drain)")] - expected) == 0


def test_cs_input_impedance():
    mu_n, Cox, W, L, V_TH, lam, R_L, VDD, V_GS_op, V_DS_op, C_gs = cas.symbols(
        "mu_n Cox W L V_TH lam R_L VDD V_GS_op V_DS_op C_gs"
    )
    Z_in = solve_impedance(parse(_ZIN), "P_in", termination="auto")
    s = cas.Symbol("s")
    expected = 1 / (s * C_gs)
    assert cas.simplify(Z_in - expected) == 0


def test_cs_output_impedance():
    mu_n, Cox, W, L, V_TH, lam, R_L, VDD, V_GS_op, V_DS_op = cas.symbols(
        "mu_n Cox W L V_TH lam R_L VDD V_GS_op V_DS_op"
    )
    Z_out = solve_impedance(parse(_ZOUT), "P_out", termination="auto")
    _, g_ds = _g_m_g_ds(mu_n, Cox, W, L, V_TH, lam, V_GS_op, V_DS_op)
    expected = R_L / (1 + g_ds * R_L)
    assert cas.simplify(cas.together(Z_out - expected)) == 0
