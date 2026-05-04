"""JFET DC tests: Shichman-Hodges drain current.

Tests both NJFET and PJFET drain current against the
expected saturation-region equation::

    I_D = pol * BETA * (V_GS_eff + VTO)^2 * (1 + LAMBDA * V_DS_eff)

where ``pol = 1`` for NJF, ``-1`` for PJF.
"""
import pytest
from sycan import cas as cas

from sycan import parse, solve_dc
from sycan.components.active.jfet import NJFET, PJFET

# --- SPICE netlist style tests (symbolic) ---

NJFET_NETLIST = """N-JFET DC test
Vgs g 0 V_GS
Vds d 0 V_DS
J1 d g 0 NJF BETA VTO LAMBDA
.end
"""

PJFET_NETLIST = """P-JFET DC test (source at positive rail)
Vgs g VDD V_GS
Vds d VDD V_DS
Vdd VDD 0 V_DD
J1 d g VDD PJF BETA VTO LAMBDA
.end
"""


def test_njfet_drain_current_symbolic():
    """NJFET: symbolic drain current in saturation matches the model."""
    V_GS, V_DS, BETA, VTO, LAMBDA = cas.symbols("V_GS V_DS BETA VTO LAMBDA")
    sol = solve_dc(parse(NJFET_NETLIST))

    assert cas.simplify(sol[cas.Symbol("V(g)")] - V_GS) == 0
    assert cas.simplify(sol[cas.Symbol("V(d)")] - V_DS) == 0

    # I_D = BETA * (V_GS + VTO)^2 * (1 + LAMBDA * V_DS)
    I_D_expected = BETA * (V_GS + VTO) ** 2 * (1 + LAMBDA * V_DS)
    # I(Vds) = -I_D (SPICE-sign: V-source branch current = -I_D flowing into drain)
    assert cas.simplify(sol[cas.Symbol("I(Vds)")] + I_D_expected) == 0


def test_pjfet_drain_current_symbolic():
    """PJFET: symbolic drain current in saturation matches the model."""
    V_GS, V_DS, V_DD, BETA, VTO, LAMBDA = cas.symbols("V_GS V_DS V_DD BETA VTO LAMBDA")
    sol = solve_dc(parse(PJFET_NETLIST))
    # For PJFET: pol = -1
    # V_GS_eff = -1 * V_GS, V_DS_eff = -1 * V_DS
    # I_D = -1 * BETA * (-V_GS + VTO)^2 * (1 + LAMBDA * (-V_DS))
    V_GS_eff = -V_GS
    V_DS_eff = -V_DS
    I_D_expected = -BETA * (V_GS_eff + VTO) ** 2 * (1 + LAMBDA * V_DS_eff)
    assert cas.simplify(sol[cas.Symbol("I(Vds)")] + I_D_expected) == 0


# --- Numeric helper tests ---

def test_njfet_operating_region():
    """NJFET region classification at various bias points."""
    j = NJFET("J1", "d", "g", "s", BETA=1e-3, VTO=2.0, LAMBDA=0.01)
    # V_GS=0, V_DS=3 → V_GS_eff=0, V_DS_eff=3, V_ov=2 → saturation
    assert j.operating_region(0.0, 3.0) == "saturation"
    # V_GS=0, V_DS=1 → V_GS_eff=0, V_DS_eff=1, V_ov=2 → triode
    assert j.operating_region(0.0, 1.0) == "triode"
    # V_GS=-3, V_DS=3 → V_GS_eff=-3, V_ov=-1 → cutoff
    assert j.operating_region(-3.0, 3.0) == "cutoff"


def test_pjfet_operating_region():
    """PJFET region classification at various bias points."""
    j = PJFET("J1", "d", "g", "s", BETA=1e-3, VTO=2.0, LAMBDA=0.01)
    # V_GS=0, V_DS=-3 → V_GS_eff=0, V_DS_eff=3, V_ov=2 → saturation
    assert j.operating_region(0.0, -3.0) == "saturation"
    # V_GS=0, V_DS=-1 → V_GS_eff=0, V_DS_eff=1, V_ov=2 → triode
    assert j.operating_region(0.0, -1.0) == "triode"
    # V_GS=+3, V_DS=-3 → V_GS_eff=-3, V_ov=-1 → cutoff
    assert j.operating_region(3.0, -3.0) == "cutoff"


def test_njfet_dc_current_saturation():
    """NJFET numeric saturation current matches I_DSS."""
    j = NJFET("J1", "d", "g", "s", BETA=1e-3, VTO=2.0, LAMBDA=0)
    # At V_GS=0, V_DS=5: V_ov=2, I_D = BETA * V_ov^2 = 1e-3 * 4 = 4 mA
    assert abs(j.dc_current(0.0, 5.0) - 4e-3) < 1e-12


def test_pjfet_dc_current_saturation():
    """PJFET numeric saturation current."""
    j = PJFET("J1", "d", "g", "s", BETA=1e-3, VTO=2.0, LAMBDA=0)
    # At V_GS=0, V_DS=-5: V_GS_eff=0, V_DS_eff=5, V_ov=2
    # I_D = -BETA * V_ov^2 = -4 mA
    assert abs(j.dc_current(0.0, -5.0) - (-4e-3)) < 1e-12


def test_njfet_dc_current_cutoff():
    """NJFET in cutoff returns zero current."""
    j = NJFET("J1", "d", "g", "s", BETA=1e-3, VTO=2.0)
    assert j.dc_current(-3.0, 5.0) == 0.0


def test_jfet_construction_errors():
    """Abstract _JFET cannot be instantiated."""
    from sycan.components.active.jfet import _JFET
    with pytest.raises(TypeError, match="abstract"):
        _JFET("Jx", "d", "g", "s", 1e-3, 2.0)


@pytest.mark.parametrize("model,pol", [("NJF", 1), ("PJF", -1)])
def test_spice_parse_jfet(model, pol):
    """SPICE parse correctly creates NJFET/PJFET from netlist."""
    netlist = f"""JFET parse test
Vgs g 0 0
Vds d 0 0
J1 d g 0 {model} 1e-3 2.0 0.01 1e-12 2e-12
.end
"""
    c = parse(netlist)
    jfets = [d for d in c.components if isinstance(d, (NJFET, PJFET))]
    assert len(jfets) == 1
    j = jfets[0]
    assert float(j.BETA) == 1e-3
    assert float(j.VTO) == 2.0
    assert float(j.LAMBDA) == 0.01
    assert float(j.C_gs) == 1e-12
    assert float(j.C_gd) == 2e-12


def test_spice_parse_jfet_bad_model():
    """SPICE parse raises on unknown JFET model."""
    netlist = """Bad JFET
Vgs g 0 0
Vds d 0 0
J1 d g 0 BAD 1e-3 2.0
.end
"""
    with pytest.raises(ValueError, match="unknown JFET model"):
        solve_dc(parse(netlist))
