"""Shockley diode DC: ``I_D = IS * (exp(V_D/(N V_T)) - 1)``.

A voltage source pins ``V_D`` across the diode. The solver should
recover the Shockley current as the V-source branch current
``I(Vd)``, which equals ``-I_D`` by the SPICE sign convention.
"""
from sycan import cas as cas

from sycan import parse, solve_dc

NETLIST = """Shockley diode
Vd a 0 V_D
D1 a 0 IS 1 V_T
.end
"""


def test_shockley_diode_iv():
    V_D, IS, V_T = cas.symbols("V_D IS V_T")
    sol = solve_dc(parse(NETLIST))

    I_D = IS * (cas.exp(V_D / V_T) - 1)
    assert cas.simplify(sol[cas.Symbol("V(a)")] - V_D) == 0
    assert cas.simplify(sol[cas.Symbol("I(Vd)")] + I_D) == 0
