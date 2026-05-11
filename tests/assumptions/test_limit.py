"""``Limit`` assumption — collapse a free symbol to its asymptotic value.

The textbook example is the inverting amplifier: as the op-amp's
open-loop gain ``A → ∞`` the closed-loop gain reduces to ``-Rf/Ri``.
The assumption engine should replicate that limit when applied to a
solved DC operating point.
"""
import sympy

from sycan import (
    Circuit,
    Limit,
    apply_assumptions,
    cas,
    solve,
    solve_ac,
    solve_dc,
)


def _inverting_amp() -> tuple[Circuit, sympy.Symbol]:
    c = Circuit("inv_amp")
    Vin = cas.Symbol("Vin")
    c.add_vsource("V1", "in", "0", Vin)
    c.add_resistor("Ri", "in", "inv", cas.Symbol("Ri"))
    c.add_resistor("Rf", "out", "inv", cas.Symbol("Rf"))
    U1 = c.add_opamp("U1", "0", "inv", "out")
    c.add_resistor("Rl", "out", "0", 1000)
    return c, U1.A


def test_limit_collapses_opamp_gain_to_ideal_inverting():
    c, A = _inverting_amp()
    sol = solve(c, mode="dc", assume=[Limit(A, sympy.oo)], simplify=True)
    Vin, Ri, Rf = cas.symbols("Vin Ri Rf")
    expected = -Rf * Vin / Ri
    assert sympy.simplify(sol[cas.Symbol("V(out)")] - expected) == 0


def test_limit_can_be_attached_to_circuit():
    """``Circuit.assume_limit(...)`` and the bare-kwarg form must agree."""
    c, A = _inverting_amp()
    c.assume_limit(A, sympy.oo)
    sol_attached = solve(c, mode="dc", simplify=True)

    c2, A2 = _inverting_amp()
    sol_kwarg = solve(c2, mode="dc",
                      assume=[Limit(A2, sympy.oo)], simplify=True)

    Vin = cas.Symbol("Vin")
    assert sympy.simplify(sol_attached[cas.Symbol("V(out)")]
                          - sol_kwarg[cas.Symbol("V(out)")]) == 0


def test_limit_applies_to_ac_solution_too():
    c, A = _inverting_amp()
    sol = solve_ac(c, assume=[Limit(A, sympy.oo)], simplify=True)
    # The s-domain expression after the gain limit must still reduce to
    # the ideal inverting form (no s dependence in this resistor-only
    # feedback network).
    Vin, Ri, Rf = cas.symbols("Vin Ri Rf")
    out_expr = sol[cas.Symbol("V(out)")]
    assert sympy.simplify(out_expr - (-Rf * Vin / Ri)) == 0


def test_limit_apply_works_on_bare_expression():
    """The Limit object's ``apply`` should also work outside the solver."""
    A = cas.Symbol("A")
    expr = (A * cas.Symbol("B")) / (A + 1)
    out = Limit(A, sympy.oo).apply(expr)
    assert sympy.simplify(out - cas.Symbol("B")) == 0


def test_apply_assumptions_helper_runs_in_order():
    """Multiple assumptions should compose in iteration order."""
    A, B = cas.symbols("A B")
    expr = (A * B) / ((A + 1) * (B + 1))
    composed = apply_assumptions(expr, [Limit(A, sympy.oo), Limit(B, sympy.oo)])
    assert sympy.simplify(composed - 1) == 0
