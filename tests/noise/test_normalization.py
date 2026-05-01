"""Validation of the ``include_noise=`` argument across components."""
from sycan import cas as cas

import pytest

from sycan.components.active import BJT, Diode, NMOS_L1
from sycan.components.basic import Capacitor, Inductor, Resistor


def test_none_yields_empty_set():
    r = Resistor("R1", "a", "b", 1)
    assert r.include_noise == frozenset()


def test_explicit_list():
    r = Resistor("R1", "a", "b", 1, include_noise=["thermal"])
    assert r.include_noise == frozenset({"thermal"})


def test_all_on_resistor_is_thermal_only():
    r = Resistor("R1", "a", "b", 1, include_noise="all")
    assert r.include_noise == frozenset({"thermal"})


def test_all_on_capacitor_is_empty():
    """Capacitors are noiseless; ``"all"`` collapses to the empty set."""
    cap = Capacitor("C1", "a", "b", 1, include_noise="all")
    assert cap.include_noise == frozenset()


def test_unsupported_kind_raises():
    with pytest.raises(ValueError, match="does not model"):
        Resistor("R1", "a", "b", 1, include_noise="shot")


def test_unrecognised_kind_raises():
    with pytest.raises(ValueError, match="unrecognised"):
        Resistor("R1", "a", "b", 1, include_noise="quantum")


def test_capacitor_rejects_thermal():
    with pytest.raises(ValueError, match="does not model"):
        Capacitor("C1", "a", "b", 1, include_noise="thermal")


def test_inductor_rejects_thermal():
    with pytest.raises(ValueError, match="does not model"):
        Inductor("L1", "a", "b", 1, include_noise="thermal")


def test_diode_supports_only_shot():
    d = Diode("D1", "a", "k", cas.Symbol("IS"), include_noise="all")
    assert d.include_noise == frozenset({"shot"})
    with pytest.raises(ValueError):
        Diode("D2", "a", "k", cas.Symbol("IS"), include_noise="thermal")


def test_bjt_emits_two_shot_sources():
    b = BJT(
        "Q1", "c", "b", "e", "NPN",
        cas.Symbol("IS"), cas.Integer(100), cas.Integer(1),
        include_noise="shot",
    )
    assert b.include_noise == frozenset({"shot"})
    src_names = {src.name for src in b.noise_sources()}
    assert src_names == {"Q1.shot.collector", "Q1.shot.base"}


def test_nmos_thermal_psd_uses_gm():
    """The MOSFET thermal noise PSD has a 4·k_B·T·γ·g_m factor."""
    m = NMOS_L1(
        "M1", "d", "g", "s",
        mu_n=cas.Symbol("mu_n", positive=True),
        Cox=cas.Symbol("Cox", positive=True),
        W=cas.Symbol("W", positive=True),
        L=cas.Symbol("L", positive=True),
        V_TH=cas.Symbol("V_TH", positive=True),
        include_noise="thermal",
    )
    sources = m.noise_sources()
    assert len(sources) == 1
    src = sources[0]
    assert src.kind == "thermal"
    # PSD must depend on the symbolic gm (which depends on V_GS_op).
    assert m.V_GS_op in src.psd.free_symbols
