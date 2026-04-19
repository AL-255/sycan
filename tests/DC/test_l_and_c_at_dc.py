"""Sanity check: at DC an inductor shorts, a capacitor opens.

The netlist has V1 driving R1 to a bus, with L1 shorting the bus to
``out`` and C1 across ``out``. In DC, L1 merges the two and C1 carries
no current, so ``V(out) = V(bus) = Vin * R2/(R1+R2)``.
"""
import sympy as sp

from sycan import parse, solve_dc

NETLIST = """L short and C open in DC
V1 in 0 Vin; down
R1 in bus R1; right
L1 bus out 1; right
R2 out 0_1 R2; down
C1 out 0_2 1; down
W1 0 0_1; right
W2 0_1 0_2; right
.end
"""


def test_inductor_shorts():
    sol = solve_dc(parse(NETLIST))
    # L1 is a short, so V(bus) == V(out).
    assert sp.simplify(sol[sp.Symbol("V(bus)")] - sol[sp.Symbol("V(out)")]) == 0


def test_capacitor_carries_no_steady_current():
    sol = solve_dc(parse(NETLIST))
    Vin, R1, R2 = sp.symbols("Vin R1 R2")
    # Current through R1 must equal current through R2 (C1 open), so
    # V(out) reduces to a pure R1/R2 divider.
    expected = Vin * R2 / (R1 + R2)
    assert sp.simplify(sol[sp.Symbol("V(out)")] - expected) == 0
