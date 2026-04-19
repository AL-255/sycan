"""Gummel-Poon DC BJT model: NPN and PNP.

Both testbenches pin V_B and V_C through ideal voltage sources so the
terminal currents become closed-form functions of the device
parameters and the bias. Each test checks the SPICE V-source branch
currents against the reduced Ebers-Moll formulas that fall out of the
full Gummel-Poon equations when ``VAF = VAR = IKF = IKR = oo`` and
``ISE = ISC = 0`` (the ideal-transistor defaults).
"""
import sympy as sp

from sycan import parse, solve_dc

NETLIST_NPN = """NPN BJT
Vbe b 0 V_BE
Vce c 0 V_CE
Q1 c b 0 NPN IS BF BR V_T
.end
"""

NETLIST_PNP = """PNP BJT
Vbe b 0 V_BE
Vce c 0 V_CE
Q1 c b 0 PNP IS BF BR V_T
.end
"""


def _ideal_emn_currents(V_BE_int, V_BC_int, IS, BF, BR, V_T):
    """Ebers-Moll transport-form currents for an ideal transistor."""
    I_BF = IS * (sp.exp(V_BE_int / V_T) - 1)
    I_BR = IS * (sp.exp(V_BC_int / V_T) - 1)
    I_CE = I_BF - I_BR
    I_BE_total = I_BF / BF
    I_BC_total = I_BR / BR
    return I_CE, I_BE_total, I_BC_total


def test_bjt_npn_gummel_poon_reduces_to_ebers_moll():
    V_BE, V_CE, IS, BF, BR, V_T = sp.symbols("V_BE V_CE IS BF BR V_T")
    sol = solve_dc(parse(NETLIST_NPN))

    # NPN internal junction voltages = external (pol = +1).
    I_CE, I_BE_total, I_BC_total = _ideal_emn_currents(
        V_BE, V_BE - V_CE, IS, BF, BR, V_T
    )
    I_C_expected = I_CE - I_BC_total
    I_B_expected = I_BE_total + I_BC_total

    # V-source branch currents (+ to - internally) equal the negative
    # of the current that SPICE-convention defines as flowing into the
    # transistor terminal.
    assert sp.simplify(sol[sp.Symbol("I(Vbe)")] + I_B_expected) == 0
    assert sp.simplify(sol[sp.Symbol("I(Vce)")] + I_C_expected) == 0


def test_bjt_pnp_gummel_poon_reduces_to_ebers_moll():
    V_BE, V_CE, IS, BF, BR, V_T = sp.symbols("V_BE V_CE IS BF BR V_T")
    sol = solve_dc(parse(NETLIST_PNP))

    # PNP flips internal voltages (pol = -1): V_BE_int = V_E - V_B, etc.
    I_CE, I_BE_total, I_BC_total = _ideal_emn_currents(
        -V_BE, V_CE - V_BE, IS, BF, BR, V_T
    )
    # Terminal currents also flip sign for PNP.
    I_C_expected = -(I_CE - I_BC_total)
    I_B_expected = -(I_BE_total + I_BC_total)

    assert sp.simplify(sol[sp.Symbol("I(Vbe)")] + I_B_expected) == 0
    assert sp.simplify(sol[sp.Symbol("I(Vce)")] + I_C_expected) == 0


def test_bjt_early_effect_nonlinearizes_collector_current():
    """When ``VAF`` is finite the base-charge factor ``q_B`` scales the
    collector transport current; I_C must gain a 1/(1 - V_BC/VAF)
    dependence. We verify this by building the model directly (the
    full G-P solve with symbolic VAF runs into sympy simplification
    limits)."""
    from sycan import Circuit, build_mna

    V_BE, V_CE, IS, BF, BR, V_T, VAF = sp.symbols("V_BE V_CE IS BF BR V_T VAF")

    circuit = Circuit()
    circuit.add_vsource("Vbe", "b", "0", V_BE)
    circuit.add_vsource("Vce", "c", "0", V_CE)
    circuit.add_bjt(
        "Q1", "c", "b", "0", "NPN",
        IS=IS, BF=BF, BR=BR, V_T=V_T, VAF=VAF,
    )

    # Expected q_B when only VAF is finite: q_1 = 1/(1 - V_BC/VAF),
    # q_2 = 0, so q_B = q_1.
    V_BC = V_BE - V_CE
    q_B = 1 / (1 - V_BC / VAF)

    I_BF = IS * (sp.exp(V_BE / V_T) - 1)
    I_BR = IS * (sp.exp(V_BC / V_T) - 1)
    I_C_expected = (I_BF - I_BR) / q_B - I_BR / BR

    sol = solve_dc(circuit)
    assert sp.simplify(sol[sp.Symbol("I(Vce)")] + I_C_expected) == 0
