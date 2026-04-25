"""Common-gate amplifier: DC bias, AC voltage gain, input impedance,
output impedance.

Topology::

         VDD
          |
          R_L
          |
          +----- drain = V_out
          |
         /
     [M1]     NMOS_L1, saturation
         \\
          |
          source = V_in

     gate = V_G_bias  (AC-grounded)

Closed-form expectations:

* **DC**:    I_D = (1/2) mu_n Cox (W/L) (V_GS - V_TH)^2 (1 + lam V_DS)
* **AC**:    A_v = (g_m + g_ds) R_L / (1 + g_ds R_L)
* **Z_in**:  (1 + g_ds R_L) / (g_m + g_ds)        (~ 1/g_m for small g_ds)
* **Z_out**: R_L / (1 + g_ds R_L)                  (~ R_L || r_o)
"""
import sympy as sp

from sycan import parse, solve_ac, solve_dc, solve_impedance

_DC = """\
CG amp DC test
Vg g 0 V_G
Vs s 0 V_S
Vd d 0 V_D
M1 d g s NMOS_L1 mu_n Cox W L V_TH lam
.end
"""

_AC_GAIN = """\
CG amp AC gain test
Vin s 0 AC v_in
Vgbias g 0 DC V_G_bias
Vdd vdd 0 DC VDD
RL vdd d R_L
M1 d g s NMOS_L1 mu_n Cox W L V_TH lam V_GS_op V_DS_op
.end
"""

_ZIN = """\
CG amp Zin test
P_in s 0 input
P_out d 0 output
Vgbias g 0 DC V_G_bias
Vdd vdd 0 DC VDD
RL vdd d R_L
M1 d g s NMOS_L1 mu_n Cox W L V_TH lam V_GS_op V_DS_op
.end
"""

_ZOUT = """\
CG amp Zout test
P_in s 0 input
P_out d 0 output
Vgbias g 0 DC V_G_bias
Vdd vdd 0 DC VDD
RL vdd d R_L
M1 d g s NMOS_L1 mu_n Cox W L V_TH lam V_GS_op V_DS_op
.end
"""


def _g_m_g_ds(mu_n, Cox, W, L, V_TH, lam, V_GS_op, V_DS_op):
    g_m = mu_n * Cox * (W / L) * (V_GS_op - V_TH) * (1 + lam * V_DS_op)
    g_ds = sp.Rational(1, 2) * mu_n * Cox * (W / L) * (V_GS_op - V_TH) ** 2 * lam
    return g_m, g_ds


# ---------------------------------------------------------------------------

def test_cg_dc_drain_current():
    V_G, V_S, V_D, mu_n, Cox, W, L, V_TH, lam = sp.symbols(
        "V_G V_S V_D mu_n Cox W L V_TH lam"
    )
    sol = solve_dc(parse(_DC))

    V_GS = V_G - V_S
    V_DS = V_D - V_S
    I_D = sp.Rational(1, 2) * mu_n * Cox * (W / L) * (V_GS - V_TH) ** 2 * (1 + lam * V_DS)
    assert sp.simplify(sol[sp.Symbol("I(Vd)")] + I_D) == 0


def test_cg_ac_voltage_gain():
    mu_n, Cox, W, L, V_TH, lam, R_L, VDD, V_GS_op, V_DS_op, v_in, V_G_bias = sp.symbols(
        "mu_n Cox W L V_TH lam R_L VDD V_GS_op V_DS_op v_in V_G_bias"
    )
    sol = solve_ac(parse(_AC_GAIN))

    g_m, g_ds = _g_m_g_ds(mu_n, Cox, W, L, V_TH, lam, V_GS_op, V_DS_op)
    expected = (g_m + g_ds) * v_in * R_L / (1 + g_ds * R_L)
    assert sp.simplify(sol[sp.Symbol("V(d)")] - expected) == 0


def test_cg_input_impedance():
    mu_n, Cox, W, L, V_TH, lam, R_L, VDD, V_GS_op, V_DS_op, V_G_bias = sp.symbols(
        "mu_n Cox W L V_TH lam R_L VDD V_GS_op V_DS_op V_G_bias"
    )
    Z_in = solve_impedance(parse(_ZIN), "P_in", termination="auto")
    g_m, g_ds = _g_m_g_ds(mu_n, Cox, W, L, V_TH, lam, V_GS_op, V_DS_op)
    expected = (1 + g_ds * R_L) / (g_m + g_ds)
    assert sp.simplify(sp.together(Z_in - expected)) == 0


def test_cg_output_impedance():
    mu_n, Cox, W, L, V_TH, lam, R_L, VDD, V_GS_op, V_DS_op, V_G_bias = sp.symbols(
        "mu_n Cox W L V_TH lam R_L VDD V_GS_op V_DS_op V_G_bias"
    )
    Z_out = solve_impedance(parse(_ZOUT), "P_out", termination="auto")
    _, g_ds = _g_m_g_ds(mu_n, Cox, W, L, V_TH, lam, V_GS_op, V_DS_op)
    expected = R_L / (1 + g_ds * R_L)
    assert sp.simplify(sp.together(Z_out - expected)) == 0
