"""Small-signal AC model for the Level-1 MOSFET, parametrised over
polarity. ``g_m`` and ``g_ds`` come out symbolically the same for NMOS
and PMOS once V_GS / V_DS are rewritten in "effective" form.

Common-source amplifier: ``R_L`` to AC-grounded supply, gate driven by
small-signal ``v_in``. Transfer function (both polarities)::

    V(d) / v_in = -g_m * R_L / (1 + g_ds * R_L)

and with ``C_gd`` included::

    V(d) / v_in = -(g_m - s*C_gd) * R_L / (1 + R_L * (g_ds + s*C_gd))
"""
from sycan import cas as cas
import pytest

from sycan import parse, solve_ac

_CS_AMP = """\
CS {mtype} amp
V1 g 0 AC v_in
V2 vdd 0 DC VDD
RL vdd d R_L
M1 d g 0 {mtype} mu_n Cox W L V_TH lam V_GS_op V_DS_op
.end
"""

_CS_AMP_CGD = """\
CS {mtype} amp with Cgd
V1 g 0 AC v_in
V2 vdd 0 DC VDD
RL vdd d R_L
M1 d g 0 {mtype} mu_n Cox W L V_TH lam V_GS_op V_DS_op 0 C_gd
.end
"""


@pytest.mark.parametrize("mtype,pol", [("NMOS_L1", 1), ("PMOS_L1", -1)])
def test_cs_amplifier_low_frequency_gain(mtype, pol):
    (
        mu_n, Cox, W, L, V_TH, lam, R_L, V_GS_op, V_DS_op, v_in, VDD,
    ) = cas.symbols(
        "mu_n Cox W L V_TH lam R_L V_GS_op V_DS_op v_in VDD"
    )
    sol = solve_ac(parse(_CS_AMP.format(mtype=mtype)))

    pol_s = cas.Integer(pol)
    V_GS_eff = pol_s * V_GS_op
    V_DS_eff = pol_s * V_DS_op
    g_m = mu_n * Cox * (W / L) * (V_GS_eff - V_TH) * (1 + lam * V_DS_eff)
    g_ds = cas.Rational(1, 2) * mu_n * Cox * (W / L) * (V_GS_eff - V_TH) ** 2 * lam

    V_out = sol[cas.Symbol("V(d)")]
    expected = -g_m * v_in * R_L / (1 + g_ds * R_L)
    assert cas.simplify(V_out - expected) == 0


@pytest.mark.parametrize("mtype,pol", [("NMOS_L1", 1), ("PMOS_L1", -1)])
def test_cs_amplifier_with_miller_capacitance(mtype, pol):
    (
        mu_n, Cox, W, L, V_TH, lam, R_L, V_GS_op, V_DS_op, v_in, VDD, C_gd,
    ) = cas.symbols(
        "mu_n Cox W L V_TH lam R_L V_GS_op V_DS_op v_in VDD C_gd"
    )
    sol = solve_ac(parse(_CS_AMP_CGD.format(mtype=mtype)))

    s = cas.Symbol("s")
    pol_s = cas.Integer(pol)
    V_GS_eff = pol_s * V_GS_op
    V_DS_eff = pol_s * V_DS_op
    g_m = mu_n * Cox * (W / L) * (V_GS_eff - V_TH) * (1 + lam * V_DS_eff)
    g_ds = cas.Rational(1, 2) * mu_n * Cox * (W / L) * (V_GS_eff - V_TH) ** 2 * lam

    expected = (
        -v_in * (g_m - s * C_gd) * R_L
        / (1 + R_L * (g_ds + s * C_gd))
    )
    V_out = sol[cas.Symbol("V(d)")]
    assert cas.simplify(cas.together(V_out - expected)) == 0
