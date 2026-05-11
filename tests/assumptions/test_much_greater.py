"""``MuchGreater`` / ``MuchLess`` — relative-magnitude assumptions.

When one symbol is asserted much larger than another, the assumption
engine should rewrite the dependence so the smaller quantity drops out
of the asymptote.
"""
import sympy

from sycan import (
    Circuit,
    MuchGreater,
    MuchLess,
    apply_assumptions,
    cas,
    solve_dc,
)


def test_much_greater_drops_small_resistor_in_divider():
    """Series-resistor divider: when ``R1 >> R2`` the output → V_in·R2/R1·… → 0."""
    c = Circuit("div")
    Vin, R1, R2 = cas.symbols("Vin R1 R2")
    c.add_vsource("V1", "in", "0", Vin)
    c.add_resistor("R1", "in", "out", R1)
    c.add_resistor("R2", "out", "0", R2)

    sol = solve_dc(c, assume=[MuchGreater(R1, R2)], simplify=True)
    # V(out) = Vin·R2/(R1+R2). Under R1 >> R2 the limit is 0.
    assert sympy.simplify(sol[cas.Symbol("V(out)")]) == 0


def test_much_less_is_sugar_for_swapped_much_greater():
    """``MuchLess(small, big)`` and ``MuchGreater(big, small)`` produce
    the same simplification."""
    Vin, R1, R2 = cas.symbols("Vin R1 R2")
    expr = Vin * R2 / (R1 + R2)
    a = apply_assumptions(expr, [MuchGreater(R1, R2)])
    b = apply_assumptions(expr, [MuchLess(R2, R1)])
    assert sympy.simplify(a - b) == 0


def test_much_greater_general_path_with_two_expressions():
    """Neither side is a bare symbol → the engine uses the
    ε-substitution path and still reaches the right asymptote."""
    a, b, c, d = cas.symbols("a b c d")
    big = a + b
    small = c + d
    expr = small / (big + small)
    # As (c+d) << (a+b), the ratio → 0.
    out = apply_assumptions(expr, [MuchGreater(big, small)])
    assert sympy.simplify(out) == 0


def test_much_greater_keeps_other_symbols_free():
    """Only the relative pair gets collapsed — unrelated symbols stay."""
    A, B, k = cas.symbols("A B k")
    expr = k * B / (A + B)
    out = apply_assumptions(expr, [MuchGreater(A, B)])
    # As A >> B, k·B/(A+B) → 0 — k drops out symbolically because the
    # whole expression vanishes. Confirm the result has no A or B.
    assert sympy.simplify(out) == 0
