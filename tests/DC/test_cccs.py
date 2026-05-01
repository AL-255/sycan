"""CCCS (SPICE F): current mirror driven by a 0 V ammeter source."""
from sycan import cas as cas

from sycan import parse, solve_dc

# Vm is a 0 V source acting as an ammeter in series with Rs. Because
# Vm sits with + at `in` and - at `mid`, I(Vm) equals the current
# Vin/Rs flowing from `in` to ground through the source + Rs.
# F1 mirrors it with gain beta, injecting beta*I(Vm) into `out`.
NETLIST = """cccs mirror
V1 in 0 Vin; down
Vm in mid 0; right
Rs mid 0_1 Rs; down
F1 0_2 out Vm beta; up
W4 out out2; right
RL out2 0_3 RL; down
W1 0 0_1; right
W2 0_1 0_2; right
W3 0_2 0_3; right
.end
"""


def test_cccs_mirror():
    sol = solve_dc(parse(NETLIST))
    Vin, Rs, RL, beta = cas.symbols("Vin Rs RL beta")
    assert cas.simplify(sol[cas.Symbol("I(Vm)")] - Vin / Rs) == 0
    assert cas.simplify(sol[cas.Symbol("V(out)")] - beta * Vin * RL / Rs) == 0
