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


@pytest.mark.parametrize("model,pol", [("NMOS_L1", 1), ("PMOS_L1", -1)])
def test_operating_region_classification(model, pol):
    """``operating_region`` returns the right region for each (V_GS, V_DS)."""
    from sycan.components.active.mosfet_l1 import NMOS_L1, PMOS_L1
    Cls = NMOS_L1 if model == "NMOS_L1" else PMOS_L1
    m = Cls(
        name="M", drain="d", gate="g", source="s",
        mu_n=1e-3, Cox=1.0, W=1.0, L=1.0,
        V_TH=0.5, lam=0.0,
    )
    # Cutoff: |V_GS| below threshold.
    assert m.operating_region(pol * 0.2, pol * 1.0) == "cutoff"
    # Saturation: |V_GS| above threshold and |V_DS| >= overdrive.
    assert m.operating_region(pol * 1.5, pol * 2.0) == "saturation"
    # Triode: |V_GS| above threshold but |V_DS| < overdrive.
    assert m.operating_region(pol * 1.5, pol * 0.2) == "triode"


@pytest.mark.parametrize("model,pol", [("NMOS_L1", 1), ("PMOS_L1", -1)])
def test_dc_current_matches_region_equation(model, pol):
    """``dc_current`` returns the per-region long-channel current."""
    from sycan.components.active.mosfet_l1 import NMOS_L1, PMOS_L1
    Cls = NMOS_L1 if model == "NMOS_L1" else PMOS_L1
    beta = 2e-3
    V_TH = 0.5
    m = Cls(
        name="M", drain="d", gate="g", source="s",
        mu_n=beta, Cox=1.0, W=1.0, L=1.0,
        V_TH=V_TH, lam=0.0,
    )
    # Cutoff -> 0.
    assert m.dc_current(pol * 0.2, pol * 1.0) == 0.0
    # Saturation: 1/2 * beta * V_ov**2.
    V_ov = 1.0  # |V_GS| = 1.5, V_TH = 0.5 -> V_ov = 1.0
    assert m.dc_current(pol * 1.5, pol * 2.0) == pytest.approx(
        pol * 0.5 * beta * V_ov ** 2
    )
    # Triode: beta * (V_ov*V_DS - 0.5*V_DS**2).
    V_DS_eff = 0.2
    expected_mag = beta * (V_ov * V_DS_eff - 0.5 * V_DS_eff ** 2)
    assert m.dc_current(pol * 1.5, pol * 0.2) == pytest.approx(pol * expected_mag)
