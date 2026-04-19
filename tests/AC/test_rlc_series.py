"""Series RLC: input impedance ``R + sL + 1/(sC)`` and resonance."""
import sympy as sp

from sycan import parse, solve_ac

NETLIST = """series RLC
V1 in 0 AC Vin; down
R1 in n1 R; right
L1 n1 n2 L; right
C1 n2 0_1 C; down
W1 0 0_1; right
.end
"""


def test_rlc_series_input_current():
    s, R, L, C, Vin = sp.symbols("s R L C Vin")
    sol = solve_ac(parse(NETLIST))
    # External current leaving V1's + terminal is -I(V1).
    i_ext = -sol[sp.Symbol("I(V1)")]
    expected = Vin / (R + s * L + 1 / (s * C))
    assert sp.simplify(i_ext - expected) == 0


def test_rlc_series_pole_polynomial():
    # The denominator of the transfer function I(V1)/Vin should factor
    # as (1 + sRC + s^2 LC)/(sC) — the canonical second-order form.
    s, R, L, C, Vin = sp.symbols("s R L C Vin")
    sol = solve_ac(parse(NETLIST))
    i_ext = -sol[sp.Symbol("I(V1)")]
    # i_ext * (1 + sRC + s^2 LC) == Vin * s * C
    assert sp.cancel(i_ext * (1 + s * R * C + s**2 * L * C) - Vin * s * C) == 0
