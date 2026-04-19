"""Lossless transmission-line ABCD/Y-parameter checks.

Classical identities verified symbolically by solve_impedance on a
terminated TLINE:

* matched load        Z_L = Z0          =>  Z_in = Z0
* quarter-wave invert sτ = jπ/2         =>  Z_in = Z0**2 / Z_L
* DC                  T-line == wire
"""
import sympy as sp

from sycan import parse, solve_dc, solve_impedance

_LOADED_TLINE = """\
Loaded tline
P_in in 0 input
T1 in 0 out 0 Z0 td
RL out 0 Z_L
.end
"""

_DC_TLINE = """\
Tline DC test
Vin in 0 V_in
T1 in 0 out 0 Z0 td
RL out 0 R_L
.end
"""


def _numer_zero(expr: sp.Expr) -> bool:
    """Return True iff the numerator of expr (after trigsimp) is zero."""
    numer = sp.fraction(sp.together(expr))[0]
    return sp.trigsimp(sp.expand(numer)) == 0


def test_tline_matched_load_reflects_Z0():
    """With the far end terminated in Z0, Z_in = Z0 at every frequency."""
    Z0, Z_L = sp.symbols("Z0 Z_L")
    c = parse(_LOADED_TLINE)
    Z_in = solve_impedance(c, "P_in", termination="z")
    assert _numer_zero(Z_in.subs(Z_L, Z0) - Z0)


def test_tline_quarter_wave_impedance_inverter():
    """At s*td = j*pi/2 the line inverts: Z_in = Z0**2 / Z_L."""
    Z0, Z_L = sp.symbols("Z0 Z_L")
    s = sp.Symbol("s")
    c = parse(_LOADED_TLINE)
    Z_in = solve_impedance(c, "P_in", termination="z", s=s)
    Z_in_qw = Z_in.subs(s * sp.Symbol("td"), sp.I * sp.pi / 2)
    assert _numer_zero(Z_in_qw - Z0 ** 2 / Z_L)


def test_tline_dc_is_a_wire():
    """At DC the inner conductor is a short: V(in) = V(out)."""
    V_in = sp.Symbol("V_in")
    c = parse(_DC_TLINE)
    sol = solve_dc(c)
    assert sp.simplify(sol[sp.Symbol("V(in)")] - V_in) == 0
    assert sp.simplify(sol[sp.Symbol("V(out)")] - V_in) == 0
