"""Vacuum-tube triode (grounded-cathode) amplifier:
DC bias, AC voltage gain, input impedance, output impedance.

Topology (grounded-cathode, the tube analogue of a common-source amp)::

          V_B = HT supply
           |
          R_L
           |
           +---- plate = V_out
           |
          ___
          \\ /    triode (Langmuir 3/2 law)
          ---
           |
           |
          grid = V_in       cathode = GND

Closed-form expectations:

* **DC**:  I_p = K * (mu V_gk + V_pk)^(3/2).
* **AC**:  A_v = -g_m R_L / (1 + g_p R_L)  (identical structure to the
                                            NMOS_L1 CS amp but with
                                            g_m and g_p from the tube).
* **Z_in**:  1 / (s C_gk)  (grid draws no DC current; C_gk is the path
                            to the grounded cathode when C_gp = 0).
* **Z_out**: R_L / (1 + g_p R_L) = R_L || r_p.
"""
from sycan import cas as cas

from sycan import parse, solve_ac, solve_dc, solve_impedance

_DC = """\
Triode DC test
Vg grid 0 V_g
Vp plate 0 V_p
X1 plate grid 0 TRIODE K mu
.end
"""

_AC_GAIN = """\
Triode AC gain test
Vin grid 0 AC v_in
Vb hv 0 DC V_B
RL hv plate R_L
X1 plate grid 0 TRIODE K mu V_g_op V_p_op
.end
"""

_ZIN = """\
Triode Zin test
P_in grid 0 input
P_out plate 0 output
Vb hv 0 DC V_B
RL hv plate R_L
X1 plate grid 0 TRIODE K mu V_g_op V_p_op C_gk
.end
"""

_ZOUT = """\
Triode Zout test
P_in grid 0 input
P_out plate 0 output
Vb hv 0 DC V_B
RL hv plate R_L
X1 plate grid 0 TRIODE K mu V_g_op V_p_op
.end
"""


def _g_m_g_p(K, mu, V_g_op, V_p_op):
    V_eff = mu * V_g_op + V_p_op
    g_p = cas.Rational(3, 2) * K * V_eff ** cas.Rational(1, 2)
    g_m = mu * g_p
    return g_m, g_p


# ---------------------------------------------------------------------------

def test_triode_dc_plate_current():
    K, mu, V_g, V_p = cas.symbols("K mu V_g V_p")
    sol = solve_dc(parse(_DC))
    I_p = K * (mu * V_g + V_p) ** cas.Rational(3, 2)
    assert cas.simplify(sol[cas.Symbol("I(Vp)")] + I_p) == 0


def test_triode_ac_voltage_gain():
    K, mu, R_L, V_B, V_g_op, V_p_op, v_in = cas.symbols(
        "K mu R_L V_B V_g_op V_p_op v_in"
    )
    sol = solve_ac(parse(_AC_GAIN))

    g_m, g_p = _g_m_g_p(K, mu, V_g_op, V_p_op)
    expected = -g_m * v_in * R_L / (1 + g_p * R_L)
    assert cas.simplify(sol[cas.Symbol("V(plate)")] - expected) == 0


def test_triode_input_impedance():
    """Z_in = 1/(s C_gk) — cathode is AC-grounded, so C_gk connects the
    grid to AC-ground directly. C_gp is set to 0 here to keep the test
    closed-form (Miller feed-through through C_gp would couple the
    plate node and complicate the answer)."""
    K, mu, R_L, V_B, V_g_op, V_p_op, C_gk = cas.symbols(
        "K mu R_L V_B V_g_op V_p_op C_gk"
    )
    c = parse(_ZIN)
    Z_in = solve_impedance(c, "P_in", termination="auto")
    s = cas.Symbol("s")
    expected = 1 / (s * C_gk)
    assert cas.simplify(Z_in - expected) == 0


def test_triode_output_impedance():
    K, mu, R_L, V_B, V_g_op, V_p_op = cas.symbols(
        "K mu R_L V_B V_g_op V_p_op"
    )
    c = parse(_ZOUT)
    Z_out = solve_impedance(c, "P_out", termination="auto")
    _, g_p = _g_m_g_p(K, mu, V_g_op, V_p_op)
    expected = R_L / (1 + g_p * R_L)
    assert cas.simplify(cas.together(Z_out - expected)) == 0
