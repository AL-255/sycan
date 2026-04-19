"""VCVS (SPICE E): ideal voltage amplifier with finite gain A."""
import sympy as sp

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
    Vin, A, RL = sp.symbols("Vin A RL")
    assert sp.simplify(sol[sp.Symbol("V(out)")] - A * Vin) == 0
    # Input draws zero current (ideal voltage-controlled port).
    assert sp.simplify(sol[sp.Symbol("I(V1)")]) == 0
    # I(E1) is from + to - internally; sourcing A*Vin/RL into the load
    # makes it negative.
    assert sp.simplify(sol[sp.Symbol("I(E1)")] + A * Vin / RL) == 0
