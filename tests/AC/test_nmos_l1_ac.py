"""Small-signal AC model for the Level-1 MOSFET, parametrised over
polarity. ``g_m`` and ``g_ds`` come out symbolically the same for NMOS
and PMOS once V_GS / V_DS are rewritten in "effective" form.

Common-source amplifier: ``R_L`` to AC-grounded supply, gate driven by
small-signal ``v_in``. Transfer function (both polarities)::

    V(d) / v_in = -g_m * R_L / (1 + g_ds * R_L)

and with ``C_gd`` included::

    V(d) / v_in = -(g_m - s*C_gd) * R_L / (1 + R_L * (g_ds + s*C_gd))
"""
import sympy as sp
import pytest

from sycan import Circuit, NMOS_L1, PMOS_L1, solve_ac


def _build_cs_amp(
    cls, mu_n, Cox, W, L, V_TH, lam, R_L, V_GS_op, V_DS_op, v_in, VDD,
    *, C_gd=0, C_gs=0,
):
    c = Circuit()
    c.add_vsource("V1", "g", "0", value=0, ac_value=v_in)
    c.add_vsource("V2", "vdd", "0", value=VDD, ac_value=0)
    c.add_resistor("RL", "vdd", "d", R_L)
    c.add(
        cls(
            "M1", "d", "g", "0",
            mu_n=mu_n, Cox=Cox, W=W, L=L, V_TH=V_TH,
            lam=lam, C_gs=C_gs, C_gd=C_gd,
            V_GS_op=V_GS_op, V_DS_op=V_DS_op,
        )
    )
    return c


@pytest.mark.parametrize("cls,pol", [(NMOS_L1, 1), (PMOS_L1, -1)])
def test_cs_amplifier_low_frequency_gain(cls, pol):
    (
        mu_n, Cox, W, L, V_TH, lam, R_L, V_GS_op, V_DS_op, v_in, VDD,
    ) = sp.symbols(
        "mu_n Cox W L V_TH lam R_L V_GS_op V_DS_op v_in VDD"
    )
    circuit = _build_cs_amp(
        cls, mu_n, Cox, W, L, V_TH, lam, R_L, V_GS_op, V_DS_op, v_in, VDD
    )
    sol = solve_ac(circuit)

    pol_s = sp.Integer(pol)
    V_GS_eff = pol_s * V_GS_op
    V_DS_eff = pol_s * V_DS_op
    g_m = mu_n * Cox * (W / L) * (V_GS_eff - V_TH) * (1 + lam * V_DS_eff)
    g_ds = sp.Rational(1, 2) * mu_n * Cox * (W / L) * (V_GS_eff - V_TH) ** 2 * lam

    V_out = sol[sp.Symbol("V(d)")]
    expected = -g_m * v_in * R_L / (1 + g_ds * R_L)
    assert sp.simplify(V_out - expected) == 0


@pytest.mark.parametrize("cls,pol", [(NMOS_L1, 1), (PMOS_L1, -1)])
def test_cs_amplifier_with_miller_capacitance(cls, pol):
    (
        mu_n, Cox, W, L, V_TH, lam, R_L, V_GS_op, V_DS_op, v_in, VDD, C_gd,
    ) = sp.symbols(
        "mu_n Cox W L V_TH lam R_L V_GS_op V_DS_op v_in VDD C_gd"
    )
    circuit = _build_cs_amp(
        cls, mu_n, Cox, W, L, V_TH, lam, R_L, V_GS_op, V_DS_op, v_in, VDD,
        C_gd=C_gd,
    )
    sol = solve_ac(circuit)

    s = sp.Symbol("s")
    pol_s = sp.Integer(pol)
    V_GS_eff = pol_s * V_GS_op
    V_DS_eff = pol_s * V_DS_op
    g_m = mu_n * Cox * (W / L) * (V_GS_eff - V_TH) * (1 + lam * V_DS_eff)
    g_ds = sp.Rational(1, 2) * mu_n * Cox * (W / L) * (V_GS_eff - V_TH) ** 2 * lam

    expected = (
        -v_in * (g_m - s * C_gd) * R_L
        / (1 + R_L * (g_ds + s * C_gd))
    )
    V_out = sol[sp.Symbol("V(d)")]
    assert sp.simplify(sp.together(V_out - expected)) == 0
