"""Unified ``solve()`` — DC = AC at s → 0 for LTI circuits.

The new entry point dispatches based on ``mode``: ``mode='ac'`` returns
the s-domain solution, ``mode='dc'`` substitutes s=0 in that same
LTI matrix (or falls back to the legacy nonlinear DC path when the
circuit contains nonlinear devices). Either way, the result must
match the dedicated ``solve_dc`` / ``solve_ac`` entry points.
"""
import pytest
import sympy

from sycan import (
    Circuit,
    cas,
    solve,
    solve_ac,
    solve_dc,
)


def _rc_lpf() -> Circuit:
    c = Circuit("rc_lpf")
    c.add_vsource("V1", "in", "0", 5)
    c.add_resistor("R", "in", "out", 1000)
    c.add_capacitor("C", "out", "0", cas.Rational(1, 10**6))
    return c


def _resistor_divider() -> Circuit:
    c = Circuit("div")
    Vin = cas.symbols("Vin")
    c.add_vsource("V1", "in", "0", Vin)
    c.add_resistor("R1", "in", "out", cas.Symbol("R1"))
    c.add_resistor("R2", "out", "0", cas.Symbol("R2"))
    return c


def test_unified_dc_lti_matches_solve_dc_on_resistor_divider():
    c1 = _resistor_divider()
    c2 = _resistor_divider()
    legacy = solve_dc(c1, simplify=True)
    unified = solve(c2, mode="dc", simplify=True)
    V_out = cas.Symbol("V(out)")
    assert sympy.simplify(legacy[V_out] - unified[V_out]) == 0


def test_unified_dc_lti_collapses_capacitor_to_open():
    """Capacitor at DC: V(out) follows V(in) — the cap is open, no
    current through R, V(out) = V(in)."""
    c1 = _rc_lpf()
    c2 = _rc_lpf()
    legacy = solve_dc(c1, simplify=True)
    unified = solve(c2, mode="dc", simplify=True)
    V_out = cas.Symbol("V(out)")
    assert legacy[V_out] == unified[V_out] == 5


def test_unified_dc_lti_collapses_inductor_to_short():
    c1 = Circuit("rl")
    c1.add_vsource("V1", "in", "0", 12)
    c1.add_resistor("R", "in", "out", 100)
    c1.add_inductor("L", "out", "0", cas.Rational(1, 1000))
    c2 = Circuit("rl")
    c2.add_vsource("V1", "in", "0", 12)
    c2.add_resistor("R", "in", "out", 100)
    c2.add_inductor("L", "out", "0", cas.Rational(1, 1000))

    legacy = solve_dc(c1, simplify=True)
    unified = solve(c2, mode="dc", simplify=True)
    V_out = cas.Symbol("V(out)")
    # Inductor short → V(out) = 0.
    assert legacy[V_out] == unified[V_out] == 0


def test_unified_ac_matches_solve_ac():
    c1 = _rc_lpf()
    c2 = _rc_lpf()
    s = cas.Symbol("s")
    legacy = solve_ac(c1, s=s)
    unified = solve(c2, mode="ac", s=s)
    V_out = cas.Symbol("V(out)")
    assert sympy.simplify(legacy[V_out] - unified[V_out]) == 0


def test_unified_dc_falls_back_to_solve_dc_for_nonlinear_circuits():
    """When the circuit contains a nonlinear device, ``solve(mode='dc')``
    must use the existing nonlinear path (``solve_dc``) rather than the
    LTI s→0 substitution — that path can't handle stamp_nonlinear
    contributions."""
    c = Circuit("cs")
    c.add_vsource("Vdd", "VDD", "0", cas.Rational(9, 5))
    c.add_vsource("Vin", "g", "0", cas.Rational(7, 10))
    c.add_resistor("RL", "VDD", "d", 10000)
    c.add_nmos_l1(
        "M1", "d", "g", "0",
        cas.Rational(1, 1000), cas.Rational(1, 500),
        10, 1, cas.Rational(1, 2),
    )
    c2 = Circuit("cs")
    c2.add_vsource("Vdd", "VDD", "0", cas.Rational(9, 5))
    c2.add_vsource("Vin", "g", "0", cas.Rational(7, 10))
    c2.add_resistor("RL", "VDD", "d", 10000)
    c2.add_nmos_l1(
        "M1", "d", "g", "0",
        cas.Rational(1, 1000), cas.Rational(1, 500),
        10, 1, cas.Rational(1, 2),
    )

    legacy = solve_dc(c)
    unified = solve(c2, mode="dc")
    V_d = cas.Symbol("V(d)")
    # Both paths must reach the same V(d) — within float tolerance for
    # the nonlinear solver's numeric output.
    assert abs(float(legacy[V_d]) - float(unified[V_d])) < 1e-6


def test_solve_rejects_unknown_mode():
    c = _resistor_divider()
    with pytest.raises(ValueError, match="mode must be 'dc' or 'ac'"):
        solve(c, mode="transient")
