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
import sympy as sp

from sycan import Circuit, solve_ac, solve_dc, solve_impedance


def _g_m_g_p(K, mu, V_g_op, V_p_op):
    V_eff = mu * V_g_op + V_p_op
    g_p = sp.Rational(3, 2) * K * V_eff ** sp.Rational(1, 2)
    g_m = mu * g_p
    return g_m, g_p


# ---------------------------------------------------------------------------

def test_triode_dc_plate_current():
    K, mu, V_g, V_p = sp.symbols("K mu V_g V_p")
    c = Circuit()
    c.add_vsource("Vg", "grid", "0", V_g)
    c.add_vsource("Vp", "plate", "0", V_p)
    c.add_triode("T1", plate="plate", grid="grid", cathode="0", K=K, mu=mu)
    sol = solve_dc(c)
    I_p = K * (mu * V_g + V_p) ** sp.Rational(3, 2)
    assert sp.simplify(sol[sp.Symbol("I(Vp)")] + I_p) == 0


def test_triode_ac_voltage_gain():
    K, mu, R_L, V_B, V_g_op, V_p_op, v_in = sp.symbols(
        "K mu R_L V_B V_g_op V_p_op v_in"
    )
    c = Circuit()
    c.add_vsource("Vin", "grid", "0", value=0, ac_value=v_in)
    c.add_vsource("Vb", "hv", "0", value=V_B, ac_value=0)
    c.add_resistor("RL", "hv", "plate", R_L)
    c.add_triode("T1", plate="plate", grid="grid", cathode="0",
                 K=K, mu=mu, V_g_op=V_g_op, V_p_op=V_p_op)
    sol = solve_ac(c)

    g_m, g_p = _g_m_g_p(K, mu, V_g_op, V_p_op)
    expected = -g_m * v_in * R_L / (1 + g_p * R_L)
    assert sp.simplify(sol[sp.Symbol("V(plate)")] - expected) == 0


def test_triode_input_impedance():
    """Z_in = 1/(s C_gk) — cathode is AC-grounded, so C_gk connects the
    grid to AC-ground directly. C_gp is set to 0 here to keep the test
    closed-form (Miller feed-through through C_gp would couple the
    plate node and complicate the answer)."""
    K, mu, R_L, V_B, V_g_op, V_p_op, C_gk = sp.symbols(
        "K mu R_L V_B V_g_op V_p_op C_gk"
    )
    c = Circuit()
    c.add_port("P_in",  "grid",  "0", "input")
    c.add_port("P_out", "plate", "0", "output")
    c.add_vsource("Vb", "hv", "0", value=V_B, ac_value=0)
    c.add_resistor("RL", "hv", "plate", R_L)
    c.add_triode("T1", plate="plate", grid="grid", cathode="0",
                 K=K, mu=mu, C_gk=C_gk,
                 V_g_op=V_g_op, V_p_op=V_p_op)

    Z_in = solve_impedance(c, "P_in", termination="auto")
    s = sp.Symbol("s")
    expected = 1 / (s * C_gk)
    assert sp.simplify(Z_in - expected) == 0


def test_triode_output_impedance():
    K, mu, R_L, V_B, V_g_op, V_p_op = sp.symbols(
        "K mu R_L V_B V_g_op V_p_op"
    )
    c = Circuit()
    c.add_port("P_in",  "grid",  "0", "input")
    c.add_port("P_out", "plate", "0", "output")
    c.add_vsource("Vb", "hv", "0", value=V_B, ac_value=0)
    c.add_resistor("RL", "hv", "plate", R_L)
    c.add_triode("T1", plate="plate", grid="grid", cathode="0",
                 K=K, mu=mu, V_g_op=V_g_op, V_p_op=V_p_op)

    Z_out = solve_impedance(c, "P_out", termination="auto")
    _, g_p = _g_m_g_p(K, mu, V_g_op, V_p_op)
    expected = R_L / (1 + g_p * R_L)
    assert sp.simplify(sp.together(Z_out - expected)) == 0
