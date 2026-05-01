"""GND element: declares an arbitrary node as the absolute zero reference.

The netlist below uses only the symbolic ground node ``gnd``; without
the ``GND1`` element all nodes would be floating. ``GND1 gnd`` pins
``V(gnd) = 0`` and the rest of the circuit collapses to a classical
series divider.
"""
from sycan import cas as cas

from sycan import parse, solve_dc

NETLIST = """gnd test
V1 a gnd Vin; down
R1 a b Ra; right
R2 b gnd_1 Rb; down
W1 gnd gnd_1; right
GND1 gnd; down
.end
"""


def test_gnd_pins_node():
    sol = solve_dc(parse(NETLIST))
    Vin, Ra, Rb = cas.symbols("Vin Ra Rb")
    assert cas.simplify(sol[cas.Symbol("V(gnd)")]) == 0
    assert cas.simplify(sol[cas.Symbol("V(a)")] - Vin) == 0
    assert cas.simplify(sol[cas.Symbol("V(b)")] - Vin * Rb / (Ra + Rb)) == 0
