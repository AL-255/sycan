"""First-order RL high-pass: ``V(out)/Vin = sL / (R + sL)``.

Output is taken across the inductor, which dominates at high ``s``.
"""
import sympy as sp

from sycan import parse, solve_ac

NETLIST = """RL high-pass
V1 in 0 AC Vin; down
R1 in out R; right
L1 out 0_1 L; down
W1 0 0_1; right
.end
"""


def test_rl_highpass_transfer():
    s, R, L, Vin = sp.symbols("s R L Vin")
    sol = solve_ac(parse(NETLIST))
    H = sol[sp.Symbol("V(out)")] / Vin
    expected = s * L / (R + s * L)
    assert sp.simplify(H - expected) == 0


def test_rl_highpass_hf_limit():
    # At s -> oo the output tracks the input.
    s, Vin = sp.symbols("s Vin")
    sol = solve_ac(parse(NETLIST))
    H = sol[sp.Symbol("V(out)")] / Vin
    assert sp.limit(H, s, sp.oo) == 1
