"""VCVS (SPICE E): ideal voltage amplifier with finite gain A."""
from sycan import cas as cas

from sycan import parse, solve_dc

NETLIST = """vcvs amplifier
V1 in 0 Vin; down
E1 out 0_1 in 0 A; down
W3 out out2; right
RL out2 0_2 RL; down
W1 0 0_1; right
W2 0_1 0_2; right
.end
"""


def test_vcvs_amplifier():
    sol = solve_dc(parse(NETLIST))
    Vin, A, RL = cas.symbols("Vin A RL")
    assert cas.simplify(sol[cas.Symbol("V(out)")] - A * Vin) == 0
    # Input draws zero current (ideal voltage-controlled port).
    assert cas.simplify(sol[cas.Symbol("I(V1)")]) == 0
    # I(E1) is from + to - internally; sourcing A*Vin/RL into the load
    # makes it negative.
    assert cas.simplify(sol[cas.Symbol("I(E1)")] + A * Vin / RL) == 0
