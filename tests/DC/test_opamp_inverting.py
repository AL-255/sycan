"""Inverting op-amp with a finite-gain VCVS model. Verifies the ideal
closed-loop gain -Rf/Ri is recovered in the limit A -> oo."""
import sympy as sp

from sycan import parse, solve_dc

# E1 out 0 0 inv A ties the non-inverting input to ground, so
# V(out) = -A * V(inv). Ri and Rf set the inverting topology.
NETLIST = """inverting op-amp
V1 in 0 Vin; down
Ri in inv Ri; right
Rf out inv Rf; left
E1 out 0_1 0_2 inv A; down
W1 0 0_2; right
W2 0_2 0_1; right
.end
"""


def test_inverting_limit():
    sol = solve_dc(parse(NETLIST))
    Vin, A, Ri, Rf = sp.symbols("Vin A Ri Rf")
    gain = sol[sp.Symbol("V(out)")] / Vin
    assert sp.simplify(sp.limit(gain, A, sp.oo) + Rf / Ri) == 0
