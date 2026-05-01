"""Behavioural linear-system building blocks.

Exercises each of the new ``sycan.components.blocks`` primitives in
isolation and then composes them into a closed first-order CT
sigma-delta loop, confirming the standard STF / NTF result.
"""
from sycan import cas as cas

from sycan import (
    Circuit,
    Integrator,
    Quantizer,
    Summer,
    TransferFunction,
    solve_ac,
)


def test_gain_block():
    c = Circuit()
    Vin = cas.Symbol("Vin")
    c.add_vsource("V1", "in", "0", value=0, ac_value=Vin)
    c.add_gain("G1", "in", "0", "out", "0", k=3)
    sol = solve_ac(c)
    assert cas.simplify(sol[cas.Symbol("V(out)")] - 3 * Vin) == 0


def test_transfer_function_first_order_lpf():
    c = Circuit()
    Vin, tau = cas.symbols("Vin tau")
    s = cas.Symbol("s")
    c.add_vsource("V1", "in", "0", value=0, ac_value=Vin)
    c.add_transfer_function("H1", "in", "0", "out", "0", H=1 / (1 + s * tau))
    sol = solve_ac(c)
    expected = Vin / (1 + s * tau)
    assert cas.simplify(sol[cas.Symbol("V(out)")] - expected) == 0


def test_integrator_pure():
    c = Circuit()
    Vin, k = cas.symbols("Vin k")
    s = cas.Symbol("s")
    c.add_vsource("V1", "in", "0", value=0, ac_value=Vin)
    c.add_integrator("I1", "in", "0", "out", "0", k=k)
    sol = solve_ac(c)
    expected = k * Vin / s
    assert cas.simplify(sol[cas.Symbol("V(out)")] - expected) == 0


def test_integrator_leaky():
    c = Circuit()
    Vin, k, a = cas.symbols("Vin k a")
    s = cas.Symbol("s")
    c.add_vsource("V1", "in", "0", value=0, ac_value=Vin)
    c.add_integrator("I1", "in", "0", "out", "0", k=k, leak=a)
    sol = solve_ac(c)
    expected = k * Vin / (s + a)
    assert cas.simplify(sol[cas.Symbol("V(out)")] - expected) == 0


def test_summer_three_inputs():
    c = Circuit()
    Va, Vb, Vc = cas.symbols("Va Vb Vc")
    c.add_vsource("VA", "a", "0", value=0, ac_value=Va)
    c.add_vsource("VB", "b", "0", value=0, ac_value=Vb)
    c.add_vsource("VC", "cn", "0", value=0, ac_value=Vc)
    c.add_summer(
        "S1", "out", "0",
        inputs=[("a", 2), ("b", -1), ("cn", "0", cas.Rational(1, 3))],
    )
    sol = solve_ac(c)
    expected = 2 * Va - Vb + Vc / 3
    assert cas.simplify(sol[cas.Symbol("V(out)")] - expected) == 0


def test_quantizer_open_loop_stf_and_noise():
    c = Circuit()
    Vin, k_q = cas.symbols("Vin k_q")
    c.add_vsource("V1", "in", "0", value=0, ac_value=Vin)
    c.add_quantizer("Q1", "in", "0", "out", "0", k_q=k_q)
    sol = solve_ac(c)
    V_q = cas.Symbol("V_q_Q1")
    expected = k_q * Vin + V_q
    assert cas.simplify(sol[cas.Symbol("V(out)")] - expected) == 0


def test_first_order_sigma_delta_loop():
    """Closed 1st-order CT sigma-delta:

        V_in --(+)--> [1/s] --> [Q] --+--> V_out
                ^                     |
                |                     |
                +-------- (-1) -------+

    Loop equations (with k_q = 1)::

        V_err = V_in - V_out
        V_int = V_err / s
        V_out = V_int + V_q

        => V_out = (V_in - V_out) / s + V_q
        => V_out = V_in/(s + 1) + V_q * s/(s + 1)

    so STF = 1/(s+1), NTF = s/(s+1).
    """
    c = Circuit()
    Vin = cas.Symbol("Vin")
    s = cas.Symbol("s")
    c.add_vsource("V1", "in", "0", value=0, ac_value=Vin)
    c.add_summer("S1", "err", "0", inputs=[("in", 1), ("out", -1)])
    c.add_integrator("I1", "err", "0", "x", "0", k=1)
    c.add_quantizer("Q1", "x", "0", "out", "0", k_q=1)

    sol = solve_ac(c)
    V_out = sol[cas.Symbol("V(out)")]
    V_q = cas.Symbol("V_q_Q1")

    # Read STF (coefficient of Vin) and NTF (coefficient of V_q).
    V_out = cas.expand(cas.together(V_out))
    STF = cas.simplify(V_out.coeff(Vin))
    NTF = cas.simplify(V_out.coeff(V_q))

    assert cas.simplify(STF - 1 / (s + 1)) == 0
    assert cas.simplify(NTF - s / (s + 1)) == 0


def test_second_order_cifb_sigma_delta_ntf_zero_at_dc():
    """Second-order Cascade-of-Integrators-Feed-Back sigma-delta.

    Two cascaded integrators with feedback at each summer; quantizer
    closes the loop. With unity coefficients the analytic NTF is

        NTF(s) = s^2 / (s^2 + s + 1)

    which has a double zero at DC — the canonical noise-shaping
    property of a 2nd-order modulator.
    """
    c = Circuit()
    Vin = cas.Symbol("Vin")
    s = cas.Symbol("s")
    c.add_vsource("V1", "in", "0", value=0, ac_value=Vin)
    c.add_summer("S1", "e1", "0", inputs=[("in", 1), ("out", -1)])
    c.add_integrator("I1", "e1", "0", "x1", "0", k=1)
    c.add_summer("S2", "e2", "0", inputs=[("x1", 1), ("out", -1)])
    c.add_integrator("I2", "e2", "0", "x2", "0", k=1)
    c.add_quantizer("Q1", "x2", "0", "out", "0", k_q=1)

    sol = solve_ac(c)
    V_out = cas.expand(cas.together(sol[cas.Symbol("V(out)")]))
    V_q = cas.Symbol("V_q_Q1")

    STF = cas.simplify(V_out.coeff(Vin))
    NTF = cas.simplify(V_out.coeff(V_q))

    assert cas.simplify(STF - 1 / (s ** 2 + s + 1)) == 0
    assert cas.simplify(NTF - s ** 2 / (s ** 2 + s + 1)) == 0
    # Double zero at DC: NTF(0) = 0 and dNTF/ds at 0 = 0.
    assert cas.simplify(NTF.subs(s, 0)) == 0
    assert cas.simplify(cas.diff(NTF, s).subs(s, 0)) == 0
