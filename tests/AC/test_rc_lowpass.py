"""First-order RC low-pass: ``V(out)/Vin = 1 / (1 + sRC)``."""
from sycan import cas as cas

from sycan import parse, solve_ac

NETLIST = """RC low-pass
V1 in 0 AC Vin; down
R1 in out R; right
C1 out 0_1 C; down
W1 0 0_1; right
.end
"""


def test_rc_lowpass_transfer():
    s, R, C, Vin = cas.symbols("s R C Vin")
    sol = solve_ac(parse(NETLIST))
    H = sol[cas.Symbol("V(out)")] / Vin
    expected = 1 / (1 + s * R * C)
    assert cas.simplify(H - expected) == 0


def test_rc_lowpass_dc_limit():
    # The transfer function should collapse to 1 at s = 0 (DC passthrough).
    s, R, C, Vin = cas.symbols("s R C Vin")
    sol = solve_ac(parse(NETLIST))
    H = sol[cas.Symbol("V(out)")] / Vin
    assert cas.simplify(H.subs(s, 0) - 1) == 0
