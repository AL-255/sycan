"""Wheatstone bridge: verify the unbalanced difference and the null at
the classical balance condition R1*R4 == R2*R3."""
from sycan import cas as cas

from sycan import parse, solve_dc

NETLIST = """wheatstone bridge
V1 a 0 Vs; down
R1 a b R1; right
R2 b 0_1 R2; down
R3 a c R3; left
R4 c 0_2 R4; down
W1 0 0_1; right
W2 0_2 0; right
.end
"""


def test_wheatstone_unbalanced():
    sol = solve_dc(parse(NETLIST))
    Vs, R1, R2, R3, R4 = cas.symbols("Vs R1 R2 R3 R4")
    diff = sol[cas.Symbol("V(b)")] - sol[cas.Symbol("V(c)")]
    expected = Vs * (R2 * R3 - R1 * R4) / ((R1 + R2) * (R3 + R4))
    assert cas.simplify(diff - expected) == 0


def test_wheatstone_balanced():
    sol = solve_dc(parse(NETLIST))
    # 1*6 == 2*3, so the bridge is balanced and V(b) == V(c).
    subs = {
        cas.Symbol("R1"): 1,
        cas.Symbol("R2"): 2,
        cas.Symbol("R3"): 3,
        cas.Symbol("R4"): 6,
    }
    vb = sol[cas.Symbol("V(b)")].subs(subs)
    vc = sol[cas.Symbol("V(c)")].subs(subs)
    assert cas.simplify(vb - vc) == 0
