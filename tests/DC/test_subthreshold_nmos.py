"""Sub-threshold MOSFET DC current — NMOS and PMOS.

A single ``polarity`` switch toggles the device. With ``V_TH`` stored
as a positive magnitude the expected drain current is

    I_D_SPICE = pol * mu_n Cox (W/L) V_T**2
              * exp((pol*V_GS - m*V_TH) / (m*V_T))
              * (1 - exp(-pol*V_DS / V_T))
"""
import sympy as sp
import pytest

from sycan import parse, solve_dc


@pytest.mark.parametrize(
    "model,pol",
    [("NMOS_subthreshold", 1), ("PMOS_subthreshold", -1)],
)
def test_subthreshold_drain_current(model, pol):
    netlist = f"""subthreshold MOSFET
Vgs g 0 VGS
Vds d 0 VDS
M1 d g 0 {model} mu_n Cox W L V_TH m V_T
.end
"""
    VGS, VDS, V_TH, mu_n, Cox, W, L, m, V_T = sp.symbols(
        "VGS VDS V_TH mu_n Cox W L m V_T"
    )
    sol = solve_dc(parse(netlist))

    assert sp.simplify(sol[sp.Symbol("V(g)")] - VGS) == 0
    assert sp.simplify(sol[sp.Symbol("V(d)")] - VDS) == 0
    assert sp.simplify(sol[sp.Symbol("I(Vgs)")]) == 0

    pol_s = sp.Integer(pol)
    V_GS_eff = pol_s * VGS
    V_DS_eff = pol_s * VDS
    I_D_mag = (
        mu_n * Cox * (W / L) * V_T**2
        * sp.exp((V_GS_eff - m * V_TH) / (m * V_T))
        * (1 - sp.exp(-V_DS_eff / V_T))
    )
    I_D_expected = pol_s * I_D_mag
    assert sp.simplify(sol[sp.Symbol("I(Vds)")] + I_D_expected) == 0
