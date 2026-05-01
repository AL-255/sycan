"""Numeric sanity check exercising SPICE engineering suffixes."""
from sycan import cas as cas

from sycan import parse, solve_dc

NETLIST = """numeric divider
V1 in 0 DC 10; down
R1 in out 1k; right
R2 out 0_1 4k; down
W1 0 0_1; right
.end
"""


def test_numeric_divider():
    sol = solve_dc(parse(NETLIST))
    # V(out) = 10 * 4k / 5k = 8 V, I(V1) = -10 / 5k = -2 mA.
    assert sol[cas.Symbol("V(out)")] == cas.Integer(8)
    assert sol[cas.Symbol("I(V1)")] == cas.Rational(-1, 500)
