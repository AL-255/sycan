"""Segmented L1 + matched-weak-inversion MOSFET (NMOS_3T / PMOS_3T).

Targets the new ``MOSFET_3T`` cell. The interesting property of this
model is that the strong-inversion (Shichman-Hodges Level 1, both
saturation and triode) and weak-inversion (exponential) pieces are
joined with a *derived* prefactor that makes the value AND the slope
of ``I_D(V_GS)`` continuous at the boundary ``V_off = V_TH + 2 m V_T``.
We verify:

* The boundary current ``I_off`` matches the L1 saturation current
  evaluated at ``V_GS_eff = V_off`` exactly.
* ``dc_current`` is C¹ at the boundary (left and right derivatives
  agree to numerical precision).
* For ``V_GS_eff << V_TH`` the cell reduces to a sub-threshold-style
  exponential — i.e. the L1 cell's flat-zero ``I_D`` is replaced with
  a realistic decaying tail.
* For ``V_GS_eff >> V_off`` the cell reduces to plain L1
  saturation / triode (parameterised over polarity).
* The region classifier returns the right label for each segment.
"""
import math

import pytest

from sycan.components.active.mosfet_3t import NMOS_3T, PMOS_3T
from sycan.components.active.mosfet_l1 import NMOS_L1, PMOS_L1


def _params() -> dict:
    return dict(
        mu_n=2.0e-4, Cox=1.0, W=2.0, L=1.0, V_TH=0.5, lam=0.0,
        m=1.5, V_T=0.026,
    )


@pytest.mark.parametrize("Cls,pol", [(NMOS_3T, +1), (PMOS_3T, -1)])
def test_boundary_current_matches_L1(Cls, pol):
    """At V_GS_eff = V_off the segment join sits on the L1 saturation curve.

    This is what makes the matching "automatic": the prefactor
    ``I_off = 2 β (m V_T)²`` derived from C¹ continuity at V_off equals
    the value the L1 saturation form produces with V_ov = 2 m V_T.
    """
    p = _params()
    m = Cls(name="M", drain="d", gate="g", source="s", **p)
    V_off = p["V_TH"] + 2 * p["m"] * p["V_T"]
    # Probe just inside strong inversion so the L1 form is the active
    # branch — using a tiny offset above V_off avoids picking up the
    # weak-inversion side at exactly the join.
    V_GS = pol * (V_off + 1e-9)
    V_DS = pol * 1.0
    I_3t = m.dc_current(V_GS, V_DS)
    beta = p["mu_n"] * p["Cox"] * p["W"] / p["L"]
    I_off_expected = pol * 0.5 * beta * (V_off - p["V_TH"]) ** 2
    assert I_3t == pytest.approx(I_off_expected, rel=1e-6, abs=1e-12)


@pytest.mark.parametrize("Cls,pol", [(NMOS_3T, +1), (PMOS_3T, -1)])
def test_C1_continuity_at_V_off(Cls, pol):
    """``I_D`` and ``dI_D/dV_GS`` agree across the strong/weak join.

    Finite-differencing across V_off catches both the value and the
    first-derivative match — that's the whole point of choosing
    V_off = V_TH + 2 m V_T and I_off = 2 β (m V_T)².
    """
    p = _params()
    m = Cls(name="M", drain="d", gate="g", source="s", **p)
    V_off = p["V_TH"] + 2 * p["m"] * p["V_T"]
    h = 1e-6  # finite-difference step in volts
    V_DS = pol * 1.0  # well into saturation on the strong side

    I_minus = m.dc_current(pol * (V_off - h), V_DS)
    I_at    = m.dc_current(pol * V_off,        V_DS)
    I_plus  = m.dc_current(pol * (V_off + h), V_DS)

    # Value: backward and forward limits straddle the join.
    assert I_minus == pytest.approx(I_at, rel=1e-3, abs=1e-12)
    assert I_plus  == pytest.approx(I_at, rel=1e-3, abs=1e-12)
    # Slope: left- and right-side derivatives (with respect to V_GS,
    # not V_GS_eff) match. The pol factor cancels because both numerator
    # and dV_GS pick up the same sign, but we measure d/dV_GS directly.
    slope_left  = (I_at - I_minus) / h
    slope_right = (I_plus - I_at) / h
    assert slope_left == pytest.approx(slope_right, rel=5e-3, abs=1e-12)


@pytest.mark.parametrize("Cls,pol", [(NMOS_3T, +1), (PMOS_3T, -1)])
def test_strong_inversion_matches_L1(Cls, pol):
    """Far above V_TH the segmented cell == plain L1 (region by region)."""
    p = _params()
    m_3t = Cls(name="M", drain="d", gate="g", source="s", **p)
    L1_Cls = NMOS_L1 if Cls is NMOS_3T else PMOS_L1
    m_l1 = L1_Cls(
        name="M", drain="d", gate="g", source="s",
        mu_n=p["mu_n"], Cox=p["Cox"], W=p["W"], L=p["L"],
        V_TH=p["V_TH"], lam=p["lam"],
    )
    # Saturation: V_DS_eff > V_GS_eff − V_TH. V_GS_eff = 1.5, V_DS_eff = 1.0.
    assert m_3t.dc_current(pol * 1.5, pol * 1.0) == pytest.approx(
        m_l1.dc_current(pol * 1.5, pol * 1.0)
    )
    # Triode: V_DS_eff = 0.2 < V_GS_eff − V_TH = 1.0.
    assert m_3t.dc_current(pol * 1.5, pol * 0.2) == pytest.approx(
        m_l1.dc_current(pol * 1.5, pol * 0.2)
    )


@pytest.mark.parametrize("Cls,pol", [(NMOS_3T, +1), (PMOS_3T, -1)])
def test_weak_inversion_decays_exponentially(Cls, pol):
    """Below V_off the tail decays at the slope-factor rate ``m V_T``."""
    p = _params()
    m = Cls(name="M", drain="d", gate="g", source="s", **p)
    V_DS = pol * 1.0  # >> V_T, so (1 - exp(-V_DS/V_T)) ≈ 1.
    # Pick two V_GS deep in the weak-inversion region. Their current
    # ratio should be exp(ΔV_GS_eff / (m V_T)) — that's the defining
    # property of the exponential tail.
    V_GS_a = pol * 0.1
    V_GS_b = pol * 0.2
    I_a = m.dc_current(V_GS_a, V_DS)
    I_b = m.dc_current(V_GS_b, V_DS)
    expected_ratio = math.exp((0.1) / (p["m"] * p["V_T"]))
    # |I_b| / |I_a| because the ratio is over magnitudes regardless of polarity.
    assert abs(I_b) / abs(I_a) == pytest.approx(expected_ratio, rel=5e-3)


@pytest.mark.parametrize("Cls,pol", [(NMOS_3T, +1), (PMOS_3T, -1)])
def test_operating_region_classification(Cls, pol):
    p = _params()
    m = Cls(name="M", drain="d", gate="g", source="s", **p)
    V_off = p["V_TH"] + 2 * p["m"] * p["V_T"]

    # Below V_off — the segmented cell calls this weak inversion (the
    # L1 model's "cutoff" replaced by a realistic exponential tail).
    assert m.operating_region(pol * 0.2, pol * 1.0) == "weak_inversion"
    # Above V_off, with V_DS large enough — saturation.
    assert m.operating_region(pol * 1.5, pol * 2.0) == "saturation"
    # Above V_off, with V_DS small — triode.
    assert m.operating_region(pol * 1.5, pol * 0.2) == "triode"
    # Just below V_off — still weak inversion.
    assert m.operating_region(pol * (V_off - 0.01), pol * 1.0) == "weak_inversion"
    # Just above V_off — strong inversion (saturation here because
    # V_DS_eff = 1.0 ≥ V_off − V_TH = 2 m V_T = 0.078).
    assert m.operating_region(pol * (V_off + 0.01), pol * 1.0) == "saturation"


@pytest.mark.parametrize("Cls,pol", [(NMOS_3T, +1), (PMOS_3T, -1)])
def test_zero_VDS_gives_zero_ID(Cls, pol):
    """KCL sanity: with V_DS = 0 the device passes no current in any region."""
    p = _params()
    m = Cls(name="M", drain="d", gate="g", source="s", **p)
    for V_GS_eff in (0.1, 0.4, 0.6, 1.5):  # span weak / near-boundary / strong
        assert m.dc_current(pol * V_GS_eff, 0.0) == pytest.approx(0.0, abs=1e-15)
