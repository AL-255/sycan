"""VCCS (SPICE G): transconductance gm into a load resistor."""
import sympy as sp

from sycan import parse, solve_dc

# G1 drives gm*V(in) from N+=0 to N-=out internally, which injects
# gm*V(in) into `out` externally.
NETLIST = """vccs amplifier
V1 in 0 Vin; down
G1 0_1 out in 0 gm; up
W3 out out2; right
RL out2 0_2 RL; down
W1 0 0_1; right
W2 0_1 0_2; right
.end
"""


def test_vccs_amplifier():
    sol = solve_dc(parse(NETLIST))
    Vin, gm, RL = sp.symbols("Vin gm RL")
    assert sp.simplify(sol[sp.Symbol("V(out)")] - gm * RL * Vin) == 0
    assert sp.simplify(sol[sp.Symbol("I(V1)")]) == 0
