"""Behavioral source (B element)."""
from sycan import cas as cas

from sycan import Circuit, solve_dc, solve_ac


def test_behavioral_current_linear_acts_like_vccs():
    """I_b = g·V(in) should give the same DC point as a VCCS."""
    Vin, R, g = cas.symbols("Vin R g", positive=True)
    V_in = cas.Symbol("V(in)")

    c = Circuit()
    c.add_vsource("V1", "in", "0", Vin)
    c.add_resistor("R1", "out", "0", R)
    # B1 sources g·V(in) into node 'out' from ground.
    c.add_behavioral_current("B1", "0", "out", g * V_in)

    sol = solve_dc(c)
    # KCL at out: 0 = -V(out)/R + g·V(in)  →  V(out) = g·R·Vin.
    assert cas.simplify(sol[cas.Symbol("V(out)")] - g * R * Vin) == 0


def test_behavioral_voltage_linear_constraint():
    """V_b = 2·V(in) should pin V(out) to twice V(in)."""
    Vin = cas.Symbol("Vin")
    V_in = cas.Symbol("V(in)")

    c = Circuit()
    c.add_vsource("V1", "in", "0", Vin)
    c.add_behavioral_voltage("B1", "out", "0", 2 * V_in)
    c.add_resistor("R1", "out", "0", 1)

    sol = solve_dc(c)
    assert cas.simplify(sol[cas.Symbol("V(out)")] - 2 * Vin) == 0


def test_behavioral_current_squarer_dc():
    """Quadratic control: I_b = k · V(in)^2 — exercises the nonlinear path."""
    V_in = cas.Symbol("V(in)")

    c = Circuit()
    c.add_vsource("V1", "in", "0", 2)  # numeric, so squarer collapses
    c.add_resistor("R1", "out", "0", 1)
    c.add_behavioral_current("B1", "0", "out", V_in ** 2)

    sol = solve_dc(c)
    # I = (V(in))^2 = 4, V(out) = 4·R = 4.
    assert cas.simplify(sol[cas.Symbol("V(out)")] - 4) == 0


def test_behavioral_current_ac_linearisation_at_op():
    """g_m for I_b=k·V(in)^2 around V(in)=V_op is 2·k·V_op."""
    V_in = cas.Symbol("V(in)")
    k = cas.Symbol("k", positive=True)

    c = Circuit()
    c.add_vsource("V1", "in", "0", 0, ac_value=1)
    c.add_resistor("R1", "out", "0", 1)
    c.add_behavioral_current(
        "B1", "0", "out", k * V_in ** 2,
        V_op_subs={V_in: 3},
    )

    sol = solve_ac(c, simplify=True)
    # gm = 2·k·3 = 6k.  V(out) = gm · V(in) · R = 6k.
    assert cas.simplify(sol[cas.Symbol("V(out)")] - 6 * k) == 0
