"""Norton: independent current source with a parallel resistor."""
from sycan import cas as cas

from sycan import parse, solve_dc

# SPICE convention: I1 drives Is from N+ to N- internally, so with
# N+=0 and N-=n the source injects +Is into node `n`.
NETLIST = """norton
I1 0 nl Is; up
W2 nl n; right
R1 n 0_1 R; down
W1 0 0_1; right
.end
"""


def test_norton_voltage():
    sol = solve_dc(parse(NETLIST))
    Is, R = cas.symbols("Is R")
    assert cas.simplify(sol[cas.Symbol("V(n)")] - Is * R) == 0
