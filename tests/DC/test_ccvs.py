"""CCVS (SPICE H): transresistance source with ammeter input."""
import sympy as sp

from sycan import parse, solve_dc

# Same ammeter topology as the CCCS test. H1 enforces
# V(out) - V(0) = rm * I(Vm).
NETLIST = """ccvs
V1 in 0 Vin; down
Vm in mid 0; right
Rs mid 0_1 Rs; down
H1 out 0_2 Vm rm; down
W4 out out2; right
RL out2 0_3 RL; down
W1 0 0_1; right
W2 0_1 0_2; right
W3 0_2 0_3; right
.end
"""


def test_ccvs():
    sol = solve_dc(parse(NETLIST))
    Vin, Rs, RL, rm = sp.symbols("Vin Rs RL rm")
    assert sp.simplify(sol[sp.Symbol("I(Vm)")] - Vin / Rs) == 0
    assert sp.simplify(sol[sp.Symbol("V(out)")] - rm * Vin / Rs) == 0
