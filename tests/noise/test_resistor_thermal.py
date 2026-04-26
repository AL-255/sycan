"""Johnson-Nyquist thermal noise of a single resistor."""
import sympy as sp

from sycan import Circuit, T_kelvin, k_B, solve_noise
from sycan.components.basic import Resistor


def test_single_resistor_thermal_noise():
    """A standalone resistor between ``out`` and ground sees its own
    thermal noise current, giving ``S_V_out = 4·k_B·T·R``."""
    R = sp.Symbol("R", positive=True)
    c = Circuit()
    c.add(Resistor("R1", "out", "0", R, include_noise="thermal"))

    total, contribs = solve_noise(c, "out", simplify=True)
    expected = 4 * k_B * T_kelvin * R

    assert sp.simplify(total - expected) == 0
    assert set(contribs) == {"R1.thermal"}
    assert sp.simplify(contribs["R1.thermal"] - expected) == 0


def test_no_noise_produces_zero_psd():
    """With ``include_noise=None`` (default), no source is emitted."""
    R = sp.Symbol("R", positive=True)
    c = Circuit()
    c.add(Resistor("R1", "out", "0", R))  # default → no noise

    total, contribs = solve_noise(c, "out", simplify=True)
    assert total == 0
    assert contribs == {}


def test_all_keyword_expands_to_supported_kinds():
    """``include_noise='all'`` expands to whatever the class supports."""
    r = Resistor("R1", "a", "b", 100, include_noise="all")
    assert r.include_noise == frozenset({"thermal"})


def test_list_argument():
    r = Resistor("R1", "a", "b", 100, include_noise=["thermal"])
    assert r.include_noise == frozenset({"thermal"})
