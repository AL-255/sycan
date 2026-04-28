"""Four-terminal segmented MOSFET (NMOS_4T / PMOS_4T) and its 3T wrapper.

Verifies:

* MOSFET_3T is a thin wrapper of MOSFET_4T — instances are also
  instances of the 4T base, the bulk is auto-tied to the source, and
  the converted port list does not double-register a bulk node.
* With ``gamma = 0`` the 4T cell is identical to the 3T wrapper at
  every bias point (no body effect).
* With ``gamma > 0`` the threshold shifts according to the standard
  long-channel formula
  ``V_TH = V_TH0 + γ (sqrt(2 φ_F + V_SB) − sqrt(2 φ_F))``.
* The SPICE parser accepts the 4T model line and routes it through
  :meth:`Circuit.add_nmos_4t` / :meth:`add_pmos_4t`.
"""
import math

import pytest

from sycan.components.active.mosfet_4t import (
    NMOS_3T,
    NMOS_4T,
    PMOS_3T,
    PMOS_4T,
)


def _params() -> dict:
    return dict(
        mu_n=2.0e-4, Cox=1.0, W=2.0, L=1.0, V_TH0=0.5, lam=0.0,
        m=1.5, V_T=0.026,
    )


@pytest.mark.parametrize("Cls3T,Cls4T", [(NMOS_3T, NMOS_4T), (PMOS_3T, PMOS_4T)])
def test_3t_is_a_4t_with_bulk_tied_to_source(Cls3T, Cls4T):
    """3T cells inherit the 4T base; bulk auto-aliases to the source.

    Concretely: a 3T instance is *also* a 4T instance, exposes the
    same four-port tuple, and answers ``m.bulk`` with the source-node
    string. ``Circuit._touch`` dedupes the duplicate node so the
    duplicate yield from ``iter_node_names`` is harmless.
    """
    p = _params()
    p3 = {k: v for k, v in p.items() if k != "V_TH0"}
    p3["V_TH"] = p["V_TH0"]
    m = Cls3T(name="M", drain="d", gate="g", source="s", **p3)
    assert isinstance(m, Cls4T)
    assert m.bulk == m.source == "s"
    # 3T is literally a 4T-with-bulk=source — the port tuple is the
    # full 4-port set inherited from the 4T base, with bulk yielding
    # the same node name as source.
    assert m.ports == ("drain", "gate", "source", "bulk")
    assert list(m.iter_node_names()) == ["d", "g", "s", "s"]


@pytest.mark.parametrize("Cls3T,Cls4T,pol", [
    (NMOS_3T, NMOS_4T, +1),
    (PMOS_3T, PMOS_4T, -1),
])
def test_4t_with_gamma_zero_matches_3t(Cls3T, Cls4T, pol):
    """``gamma = 0`` removes the body effect — 4T == 3T everywhere."""
    p = _params()
    p3 = {k: v for k, v in p.items() if k != "V_TH0"}
    p3["V_TH"] = p["V_TH0"]
    m3 = Cls3T(name="M", drain="d", gate="g", source="s", **p3)
    m4 = Cls4T(name="M", drain="d", gate="g", source="s", bulk="s", **p)
    for V_GS in (0.2, 0.6, 1.0, 1.5):
        for V_DS in (0.05, 0.5, 1.0, 2.0):
            i3 = m3.dc_current(pol * V_GS, pol * V_DS)
            # 4T with bulk == source: pass V_BS = 0 explicitly.
            i4 = m4.dc_current(pol * V_GS, pol * V_DS, 0.0)
            assert i3 == pytest.approx(i4, rel=1e-12, abs=1e-18)


@pytest.mark.parametrize("Cls,pol", [(NMOS_4T, +1), (PMOS_4T, -1)])
def test_body_effect_shifts_threshold(Cls, pol):
    """Non-zero ``V_SB`` raises ``V_TH`` per the long-channel formula."""
    gamma = 0.4   # √V — typical long-channel value
    phi   = 0.7   # 2 φ_F
    V_TH0 = 0.5
    p = _params()
    p["V_TH0"] = V_TH0
    p["gamma"] = gamma
    p["phi"]   = phi
    m = Cls(name="M", drain="d", gate="g", source="s", bulk="b", **p)

    for V_SB in (0.0, 0.5, 1.0, 2.0):
        # In the polarity-aware convention V_BS = V_B − V_S, and
        # V_SB_eff (the magnitude that enters the body-effect sqrt)
        # equals -pol * V_BS. To probe a positive V_SB_eff = V_SB we
        # set V_BS = -pol * V_SB.
        V_BS = -pol * V_SB
        # Bias deep in saturation so the saturation equation tells us
        # V_TH directly: I_D = β/2 · (V_GS_eff − V_TH)^2.
        V_GS_eff = 1.5
        V_DS_eff = 2.0
        I_D = m.dc_current(pol * V_GS_eff, pol * V_DS_eff, V_BS)
        beta = p["mu_n"] * p["Cox"] * p["W"] / p["L"]
        # Recover V_TH from I_D and compare against the formula.
        V_OV_recovered = math.sqrt(2 * abs(I_D) / beta)
        V_TH_recovered = V_GS_eff - V_OV_recovered
        V_TH_expected  = V_TH0 + gamma * (math.sqrt(phi + V_SB) - math.sqrt(phi))
        assert V_TH_recovered == pytest.approx(V_TH_expected, rel=1e-6, abs=1e-9)


@pytest.mark.parametrize("Cls,pol", [(NMOS_4T, +1), (PMOS_4T, -1)])
def test_body_effect_pushes_device_into_cutoff(Cls, pol):
    """Larger V_SB should monotonically *decrease* I_D in strong inversion."""
    p = _params()
    p["gamma"] = 0.5
    m = Cls(name="M", drain="d", gate="g", source="s", bulk="b", **p)
    V_GS_eff = 1.0
    V_DS_eff = 2.0
    last_abs = math.inf
    for V_SB in (0.0, 0.3, 0.7, 1.5):
        V_BS = -pol * V_SB
        I_D = m.dc_current(pol * V_GS_eff, pol * V_DS_eff, V_BS)
        assert abs(I_D) < last_abs, (
            f"V_SB={V_SB} should reduce |I_D| but got {abs(I_D):.3e}"
            f" (previous: {last_abs:.3e})"
        )
        last_abs = abs(I_D)


def test_spice_parser_accepts_4t_models():
    """The SPICE parser routes the ``NMOS_4T`` / ``PMOS_4T`` keywords."""
    from sycan import parse
    netlist = """4T CMOS smoke test
Vdd VDD 0 1.8
Vin in  0 0.9
MN  out in 0 0   NMOS_4T 8e-4 1 1 1 0.45 0 0.4 0.7
MP  out in VDD VDD PMOS_4T 4e-4 1 1 1 0.45 0 0.4 0.7
.end
"""
    c = parse(netlist)
    mn = next(d for d in c.components if d.name == "MN")
    mp = next(d for d in c.components if d.name == "MP")
    assert isinstance(mn, NMOS_4T) and not isinstance(mn, NMOS_3T)
    assert isinstance(mp, PMOS_4T) and not isinstance(mp, PMOS_3T)
    assert mn.bulk == "0"
    assert mp.bulk == "VDD"
    # Optional kwargs landed on the right fields.
    assert float(mn.gamma) == 0.4
    assert float(mn.phi)   == 0.7
    assert float(mp.gamma) == 0.4
