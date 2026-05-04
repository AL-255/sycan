"""Sinusoidal waveform sources in AC analysis.

When ``waveform="sine"`` is set on a voltage or current source, the AC
stamp injects the Laplace transform of ``A·sin(ω·t + φ)``:

    A · (s·sin(φ) + ω·cos(φ)) / (s² + ω²)
"""
from sycan import cas as cas

from sycan import Circuit, solve_ac, solve_dc


def _sine_expr(amplitude, frequency, phase=0):
    """Expected Laplace transform of ``A·sin(2π·f·t + φ)``."""
    s = cas.Symbol("s")
    omega = 2 * cas.pi * frequency
    return amplitude * (s * cas.sin(phase) + omega * cas.cos(phase)) / (
        s**2 + omega**2
    )


# -- Voltage source sine ---------------------------------------------------

def test_vsource_sine_zero_phase():
    """Voltage source sine (φ=0) into a resistive load."""
    c = Circuit("test")
    A, f = cas.symbols("A f")
    c.add_vsource("V1", "in", "0", 0, waveform="sine", amplitude=A, frequency=f)
    c.add_resistor("R1", "in", "0", 1)
    sol = solve_ac(c)
    expected = _sine_expr(A, f, 0)
    assert cas.simplify(sol[cas.Symbol("V(in)")] - expected) == 0


def test_vsource_sine_phase_pi_half():
    """Voltage source sine with φ=π/2 (cosine)."""
    c = Circuit("test")
    A = cas.Symbol("A")
    f = cas.Symbol("freq")
    c.add_vsource("V1", "in", "0", 0, waveform="sine", amplitude=A,
                   frequency=f, phase=cas.pi / 2)
    c.add_resistor("R1", "in", "0", 1)
    sol = solve_ac(c)
    s = cas.Symbol("s")
    omega = 2 * cas.pi * f
    expected = A * s / (s**2 + omega**2)
    assert cas.simplify(sol[cas.Symbol("V(in)")] - expected) == 0


def test_vsource_sine_voltage_divider():
    """Sine waveform through a resistive divider."""
    c = Circuit("test")
    A, f, Ra, Rb = cas.symbols("A f Ra Rb")
    c.add_vsource("V1", "in", "0", 0, waveform="sine", amplitude=A, frequency=f)
    c.add_resistor("R1", "in", "mid", Ra)
    c.add_resistor("R2", "mid", "0", Rb)
    sol = solve_ac(c)
    V_sine = _sine_expr(A, f, 0)
    expected = Rb * V_sine / (Ra + Rb)
    assert cas.simplify(sol[cas.Symbol("V(mid)")] - expected) == 0


def test_vsource_sine_dc_offset():
    """DC analysis of a sine source uses ``value`` as the offset."""
    c = Circuit("test")
    c.add_vsource("V1", "in", "0", 5, waveform="sine", amplitude=1, frequency=1e3)
    c.add_resistor("R1", "in", "0", 1e3)
    sol = solve_dc(c)
    assert float(sol[cas.Symbol("V(in)")]) == 5.0


# -- Current source sine ---------------------------------------------------

def test_isource_sine_zero_phase():
    """Current source sine (φ=0) into a resistive load.

    With n_plus=0, n_minus=n the source injects current into node n,
    producing a positive voltage across the resistor.
    """
    c = Circuit("test")
    A, f, R = cas.symbols("A f R")
    c.add_isource("I1", "0", "n", 0, waveform="sine", amplitude=A, frequency=f)
    c.add_resistor("R1", "n", "0", R)
    sol = solve_ac(c)
    expected = _sine_expr(A, f, 0) * R
    assert cas.simplify(sol[cas.Symbol("V(n)")] - expected) == 0


def test_isource_sine_dc_offset():
    """DC analysis of a sine current source uses ``value`` as the offset."""
    c = Circuit("test")
    c.add_isource("I1", "0", "n", 1e-3, waveform="sine", amplitude=1, frequency=1e3)
    c.add_resistor("R1", "n", "0", 1e3)
    sol = solve_dc(c)
    assert float(sol[cas.Symbol("V(n)")] - 1.0) == 0.0


# -- Construction errors ---------------------------------------------------

def test_vsource_sine_missing_params_raises():
    """Sine waveform requires both amplitude and frequency."""
    from sycan.components.basic.voltage_source import VoltageSource
    import pytest
    with pytest.raises(ValueError, match="amplitude and frequency"):
        VoltageSource("V1", "a", "0", 0, waveform="sine", frequency=1)


def test_vsource_unknown_waveform_raises():
    """Unknown waveform names raise ValueError."""
    from sycan.components.basic.voltage_source import VoltageSource
    import pytest
    with pytest.raises(ValueError, match="unknown waveform"):
        VoltageSource("V1", "a", "0", 0, waveform="triangle")

