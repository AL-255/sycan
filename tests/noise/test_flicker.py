"""Flicker (1/f) noise tests."""
from sycan import cas as cas

from sycan import Circuit, solve_noise
from sycan.mna import freq


def test_mosfet_l1_flicker_noise():
    """MOSFET_L1 with flicker noise emits a flicker noise source."""
    from sycan.components.active.mosfet_l1 import NMOS_L1

    c = Circuit("flicker_test")
    c.add_vsource("Vdd", "vdd", "0", 5)
    c.add_resistor("RD", "vdd", "out", 1e3)
    c.add(NMOS_L1("M1", "out", "in", "0",
                   mu_n=0.05, Cox=2e-3, W=10e-6, L=1e-6, V_TH=0.5,
                   V_GS_op=0.7, V_DS_op=2.0,
                   KF=1e-25, include_noise="flicker"))
    c.add_vsource("Vin", "in", "0", value=0, ac_value=0)

    total, contribs = solve_noise(c, "out", simplify=False)
    assert "M1.flicker" in contribs


def test_bjt_flicker_noise():
    """BJT with flicker noise emits a flicker noise source."""
    from sycan.components.active.bjt import BJT
    from sycan import NoiseSource

    bjt = BJT("Q1", "c", "b", "e", "NPN",
              IS=1e-15, BF=100, BR=1,
              KF=1e-16, include_noise="flicker")
    sources = bjt.noise_sources()
    flicker_ns = [ns for ns in sources if ns.kind == "flicker"]
    assert len(flicker_ns) == 1
    assert flicker_ns[0].name == "Q1.flicker"
    assert freq in flicker_ns[0].psd.free_symbols


def test_mosfet_4t_flicker_noise():
    """MOSFET_4T with flicker noise."""
    from sycan.components.active.mosfet_4t import NMOS_4T

    m = NMOS_4T("M1", "d", "g", "s", "b",
                mu_n=0.05, Cox=2e-3, W=10e-6, L=1e-6, V_TH0=0.5,
                KF=1e-25, AF=1, EF=1, include_noise="flicker")
    sources = m.noise_sources()
    flicker_ns = [ns for ns in sources if ns.kind == "flicker"]
    assert len(flicker_ns) == 1
    assert freq in flicker_ns[0].psd.free_symbols


def test_flicker_disabled_by_default():
    """Flicker noise is only emitted when explicitly requested."""
    from sycan.components.active.mosfet_l1 import NMOS_L1

    m = NMOS_L1("M1", "d", "g", "s",
                mu_n=0.05, Cox=2e-3, W=10e-6, L=1e-6, V_TH=0.5,
                KF=1e-25)
    sources = m.noise_sources()
    flicker_ns = [ns for ns in sources if ns.kind == "flicker"]
    assert len(flicker_ns) == 0


def test_subthreshold_flicker():
    """Subthreshold MOSFET supports flicker noise."""
    from sycan.components.active.mosfet_subthreshold import NMOS_subthreshold

    m = NMOS_subthreshold("M1", "d", "g", "s",
                          mu_n=0.05, Cox=2e-3, W=10e-6, L=1e-6, V_TH=0.5,
                          KF=1e-25, include_noise="flicker")
    sources = m.noise_sources()
    flicker_ns = [ns for ns in sources if ns.kind == "flicker"]
    assert len(flicker_ns) == 1
    assert freq in flicker_ns[0].psd.free_symbols
