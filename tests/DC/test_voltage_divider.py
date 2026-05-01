"""Resistive voltage divider driven by an ideal V-source."""
from sycan import cas as cas

from sycan import parse, solve_dc

NETLIST = """voltage divider
V1 in 0 Vin; down
R1 in mid Ra; right
R2 mid 0_1 Rb; down
W1 0 0_1; right
.end
"""


def test_voltage_divider():
    sol = solve_dc(parse(NETLIST))
    Vin, Ra, Rb = cas.symbols("Vin Ra Rb")
    assert cas.simplify(sol[cas.Symbol("V(in)")] - Vin) == 0
    assert cas.simplify(sol[cas.Symbol("V(mid)")] - Rb * Vin / (Ra + Rb)) == 0
    # I(V1) is defined from + to - internally, so the source reports a
    # negative current when it is sourcing power into the load.
    assert cas.simplify(sol[cas.Symbol("I(V1)")] + Vin / (Ra + Rb)) == 0
