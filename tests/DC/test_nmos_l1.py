"""Shichman-Hodges Level 1 MOSFET (saturation) — DC drain current.

Parametrised over polarity: a single ``polarity`` switch toggles
between NMOS and PMOS. With ``V_TH`` stored as a positive magnitude
for both, the expected drain current is

    I_D_SPICE = pol * (1/2) mu_n Cox (W/L)
              * (pol*V_GS - V_TH)**2 * (1 + lam * pol*V_DS)

where ``pol = +1`` for NMOS and ``-1`` for PMOS.
"""
import sympy as sp
import pytest

from sycan import parse, solve_dc


@pytest.mark.parametrize("model,pol", [("NMOS_L1", 1), ("PMOS_L1", -1)])
def test_shichman_hodges_drain_current(model, pol):
    netlist = f"""MOSFET DC
Vgs g 0 V_GS
Vds d 0 V_DS
M1 d g 0 {model} mu_n Cox W L V_TH lam
.end
"""
    V_GS, V_DS, mu_n, Cox, W, L, V_TH, lam = sp.symbols(
        "V_GS V_DS mu_n Cox W L V_TH lam"
    )
    sol = solve_dc(parse(netlist))

    pol_s = sp.Integer(pol)
    V_GS_eff = pol_s * V_GS
    V_DS_eff = pol_s * V_DS
    I_D_mag = (
        sp.Rational(1, 2)
        * mu_n * Cox * (W / L)
        * (V_GS_eff - V_TH) ** 2
        * (1 + lam * V_DS_eff)
    )
    I_D_expected = pol_s * I_D_mag  # SPICE sign (into drain)

    assert sp.simplify(sol[sp.Symbol("V(g)")] - V_GS) == 0
    assert sp.simplify(sol[sp.Symbol("V(d)")] - V_DS) == 0
    assert sp.simplify(sol[sp.Symbol("I(Vgs)")]) == 0
    assert sp.simplify(sol[sp.Symbol("I(Vds)")] + I_D_expected) == 0
