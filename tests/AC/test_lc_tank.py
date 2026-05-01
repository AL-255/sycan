"""Parallel LC tank driven by a current source.

For ``I1 0 n Is`` (injects +Is into ``n``) the tank impedance seen from
``n`` is ``Z = sL / (1 + s^2 L C)``, so ``V(n) = Is * Z``. At the tank
frequency ``s = j/sqrt(LC)`` the denominator vanishes -> infinite Q.
"""
from sycan import cas as cas

from sycan import parse, solve_ac

NETLIST = """LC tank
I1 0 nl AC Is; up
W_top nl n; right
L1 n 0_1 L; down
W_mid n n2; right
C1 n2 0_2 C; down
W1 0 0_1; right
W2 0_1 0_2; right
.end
"""


def test_lc_tank_impedance():
    s, L, C, Is = cas.symbols("s L C Is")
    sol = solve_ac(parse(NETLIST))
    V_n = sol[cas.Symbol("V(n)")]
    expected = Is * s * L / (1 + s**2 * L * C)
    assert cas.simplify(V_n - expected) == 0


def test_lc_tank_pole_polynomial():
    # The transfer function V(n)/Is has denominator (1 + s^2 L C), which
    # puts the resonance at s = I/sqrt(LC). We verify the polynomial
    # identity rather than substituting a value that depends on sqrt
    # simplification rules.
    s, L, C, Is = cas.symbols("s L C Is")
    sol = solve_ac(parse(NETLIST))
    V_n = sol[cas.Symbol("V(n)")]
    assert cas.cancel(V_n * (1 + s**2 * L * C) - Is * s * L) == 0
