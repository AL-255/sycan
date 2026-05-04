"""Varactor: voltage-controlled capacitor."""
from sycan import cas as cas

from sycan import Circuit, solve_ac


def test_varactor_unbiased_matches_capacitor():
    """V_op=None -> small-signal C = C0, behaves like a normal capacitor."""
    R, C0 = cas.symbols("R C0", positive=True)
    c = Circuit()
    c.add_vsource("V1", "in", "0", 0, ac_value=1)
    c.add_resistor("R1", "in", "out", R)
    c.add_varactor("D1", "out", "0", C0)

    sol = solve_ac(c, simplify=True)
    s = cas.Symbol("s")
    # Same as the RC low-pass: 1/(1 + sRC).
    H_expected = 1 / (1 + s * R * C0)
    assert cas.simplify(sol[cas.Symbol("V(out)")] - H_expected) == 0


def test_varactor_biased_capacitance_scales():
    """Apply V_op = 0 explicitly: C(0) = C0."""
    R, C0, V_J = cas.symbols("R C0 V_J", positive=True)
    c = Circuit()
    c.add_vsource("V1", "in", "0", 0, ac_value=1)
    c.add_resistor("R1", "in", "out", R)
    c.add_varactor("D1", "out", "0", C0, V_J=V_J, M=cas.Rational(1, 2), V_op=0)

    sol = solve_ac(c, simplify=True)
    s = cas.Symbol("s")
    H_expected = 1 / (1 + s * R * C0)
    assert cas.simplify(sol[cas.Symbol("V(out)")] - H_expected) == 0
