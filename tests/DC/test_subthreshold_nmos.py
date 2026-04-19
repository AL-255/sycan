"""Sub-threshold NMOS: V_GS and V_DS are pinned by V-sources so the
closed-form drain current is recoverable symbolically.

The reported supply current ``I(Vds)`` equals ``-I_D`` because SPICE
defines the V-source branch current from + to - internally while the
MOSFET pulls current out of the drain externally.
"""
import sympy as sp

from sycan import parse, solve_dc

NETLIST = """subthreshold NMOS
Vgs g 0 VGS; down
Vds d 0 VDS; down
M1 d g 0 NMOS_subthreshold mu_n Cox W L V_TH m V_T
.end
"""


def test_drain_current_matches_subthreshold_equation():
    VGS, VDS, V_TH, mu_n, Cox, W, L, m, V_T = sp.symbols(
        "VGS VDS V_TH mu_n Cox W L m V_T"
    )

    sol = solve_dc(parse(NETLIST))

    # Node voltages pinned by the two V-sources.
    assert sp.simplify(sol[sp.Symbol("V(g)")] - VGS) == 0
    assert sp.simplify(sol[sp.Symbol("V(d)")] - VDS) == 0

    # Gate draws zero current.
    assert sp.simplify(sol[sp.Symbol("I(Vgs)")]) == 0

    # I(Vds) = -I_D via the sub-threshold equation.
    I_D = (
        mu_n * Cox * (W / L) * V_T**2
        * sp.exp((VGS - m * V_TH) / (m * V_T))
        * (1 - sp.exp(-VDS / V_T))
    )
    assert sp.simplify(sol[sp.Symbol("I(Vds)")] + I_D) == 0
