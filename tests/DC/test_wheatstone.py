"""Wheatstone bridge: verify the unbalanced difference and the null at
the classical balance condition R1*R4 == R2*R3."""
import sympy as sp

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
    Vs, R1, R2, R3, R4 = sp.symbols("Vs R1 R2 R3 R4")
    diff = sol[sp.Symbol("V(b)")] - sol[sp.Symbol("V(c)")]
    expected = Vs * (R2 * R3 - R1 * R4) / ((R1 + R2) * (R3 + R4))
    assert sp.simplify(diff - expected) == 0


def test_wheatstone_balanced():
    sol = solve_dc(parse(NETLIST))
    # 1*6 == 2*3, so the bridge is balanced and V(b) == V(c).
    subs = {
        sp.Symbol("R1"): 1,
        sp.Symbol("R2"): 2,
        sp.Symbol("R3"): 3,
        sp.Symbol("R4"): 6,
    }
    vb = sol[sp.Symbol("V(b)")].subs(subs)
    vc = sol[sp.Symbol("V(c)")].subs(subs)
    assert sp.simplify(vb - vc) == 0
