"""Lossless transmission-line ABCD/Y-parameter checks.

Classical identities verified symbolically by solve_impedance on a
terminated TLINE:

* matched load        Z_L = Z0          =>  Z_in = Z0
* quarter-wave invert sτ = jπ/2         =>  Z_in = Z0**2 / Z_L
* DC                  T-line == wire
"""
import sympy as sp

from sycan import Circuit, solve_dc, solve_impedance


def _loaded_tline(Z_L_symbol):
    Z0, td = sp.symbols("Z0 td", positive=True)
    c = Circuit()
    c.add_port("P_in", "in", "0", "input")
    c.add_tline("T1", "in", "0", "out", "0", Z0, td)
    c.add_resistor("RL", "out", "0", Z_L_symbol)
    return c, Z0, td


def test_tline_matched_load_reflects_Z0():
    """With the far end terminated in Z0, Z_in = Z0 at every frequency."""
    Z_L = sp.Symbol("Z_L", positive=True)
    c, Z0, td = _loaded_tline(Z_L)
    Z_in = solve_impedance(c, "P_in", termination="z")
    # Collapse to Z0 when Z_L = Z0.
    assert sp.simplify(Z_in.subs(Z_L, Z0) - Z0) == 0


def test_tline_quarter_wave_impedance_inverter():
    """At s*td = j*pi/2 the line inverts: Z_in = Z0**2 / Z_L."""
    Z_L = sp.Symbol("Z_L", positive=True)
    c, Z0, td = _loaded_tline(Z_L)
    s = sp.Symbol("s")
    Z_in = solve_impedance(c, "P_in", termination="z", s=s)
    # Quarter-wave condition.
    Z_in_qw = Z_in.subs(s * td, sp.I * sp.pi / 2)
    assert sp.simplify(sp.together(Z_in_qw - Z0 ** 2 / Z_L)) == 0


def test_tline_dc_is_a_wire():
    """At DC the inner conductor is a short: V(in) = V(out)."""
    V_in, R_L = sp.symbols("V_in R_L", positive=True)
    Z0, td = sp.symbols("Z0 td", positive=True)
    c = Circuit()
    c.add_vsource("Vin", "in", "0", V_in)
    c.add_tline("T1", "in", "0", "out", "0", Z0, td)
    c.add_resistor("RL", "out", "0", R_L)
    sol = solve_dc(c)
    assert sp.simplify(sol[sp.Symbol("V(in)")] - V_in) == 0
    assert sp.simplify(sol[sp.Symbol("V(out)")] - V_in) == 0
