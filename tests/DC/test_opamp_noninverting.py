"""Non-inverting op-amp with a finite-gain VCVS model. Verifies the
ideal closed-loop gain 1 + Rf/Rg is recovered in the limit A -> oo."""
from sycan import cas as cas

from sycan import parse, solve_dc

# E1 out 0 in inv A : V(out) = A*(V(in) - V(inv)). Feedback ladder
# Rf (out->inv) and Rg (inv->ground) sets the closed-loop gain.
NETLIST = """non-inverting op-amp
V1 in 0 Vin; down
E1 out 0_1 in inv A; down
Rf out inv Rf; left
Rg inv 0_2 Rg; down
W1 0 0_2; right
W2 0_2 0_1; right
.end
"""


def test_noninverting_limit():
    sol = solve_dc(parse(NETLIST))
    Vin, A, Rf, Rg = cas.symbols("Vin A Rf Rg")
    gain = sol[cas.Symbol("V(out)")] / Vin
    assert cas.simplify(cas.limit(gain, A, cas.oo) - (1 + Rf / Rg)) == 0


def test_noninverting_virtual_short():
    sol = solve_dc(parse(NETLIST))
    Vin, A = cas.symbols("Vin A")
    # As A -> oo the op-amp drives V(inv) toward V(in) (virtual short).
    assert cas.simplify(cas.limit(sol[cas.Symbol("V(inv)")], A, cas.oo) - Vin) == 0
