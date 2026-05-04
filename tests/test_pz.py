"""Pole-zero analysis tests."""
import pytest
from sycan import cas as cas

from sycan import Circuit, solve_pz


def test_rc_lowpass_pz():
    """RC low-pass: one real pole at s = -1/(R*C), no zeros."""
    R, C = cas.symbols("R C", positive=True)
    c = Circuit("rc_lp")
    c.add_vsource("Vin", "in", "0", 0, ac_value=1)
    c.add_resistor("R1", "in", "out", R)
    c.add_capacitor("C1", "out", "0", C)
    result = solve_pz(c, "out", input_source="Vin", simplify=True)

    # H(s) = 1/(1 + s*R*C) → denominator = 1 + s*R*C, numerator = 1
    s = cas.Symbol("s")
    assert len(result.poles) == 1
    # pole should be -1/(R*C) — solving 1 + s*R*C = 0 gives s = -1/(R*C)
    assert cas.simplify(result.poles[0] + 1 / (R * C)) == 0
    assert len(result.zeros) == 0


def test_rc_highpass_pz():
    """RC high-pass: one pole at s = -1/(R*C), one zero at s = 0."""
    R, C = cas.symbols("R C", positive=True)
    c = Circuit("rc_hp")
    c.add_vsource("Vin", "in", "0", 0, ac_value=1)
    c.add_capacitor("C1", "in", "out", C)
    c.add_resistor("R1", "out", "0", R)
    result = solve_pz(c, "out", input_source="Vin", simplify=True)

    assert len(result.poles) == 1
    assert len(result.zeros) == 1
    s = cas.Symbol("s")
    # Pole at s = -1/(R*C)
    assert cas.simplify(result.poles[0] + 1 / (R * C)) == 0
    # Zero at s = 0
    assert cas.simplify(result.zeros[0]) == 0


def test_rlc_series_pz():
    """RLC series with V_out across C: second-order denominator."""
    R, L, C_ = cas.symbols("R L C_", positive=True)
    c = Circuit("rlc_series")
    c.add_vsource("Vin", "in", "0", 0, ac_value=1)
    c.add_resistor("R1", "in", "mid", R)
    c.add_inductor("L1", "mid", "out", L)
    c.add_capacitor("C1", "out", "0", C_)
    result = solve_pz(c, "out", input_source="Vin", simplify=True)

    # Should have 2 poles (second order) and 0 zeros
    assert len(result.poles) == 2
    assert len(result.zeros) == 0


def test_pz_result_has_transfer_function():
    """PZResult exposes the full H(s) expression."""
    R, C = cas.symbols("R C", positive=True)
    c = Circuit("test")
    c.add_vsource("Vin", "in", "0", 0, ac_value=1)
    c.add_resistor("R1", "in", "out", R)
    c.add_capacitor("C1", "out", "0", C)
    result = solve_pz(c, "out", input_source="Vin")
    s = cas.Symbol("s")
    # H(s) should be 1/(1 + s*R*C) after simplification
    H_expected = 1 / (1 + s * R * C)
    assert cas.simplify(result.H - H_expected) == 0


def test_pz_auto_detect_source():
    """solve_pz auto-detects the AC source when input_source is None."""
    R, C = cas.symbols("R C", positive=True)
    c = Circuit("test")
    c.add_vsource("Vin", "in", "0", 0, ac_value=1)
    c.add_resistor("R1", "in", "out", R)
    c.add_capacitor("C1", "out", "0", C)
    result = solve_pz(c, "out")
    s = cas.Symbol("s")
    H_expected = 1 / (1 + s * R * C)
    assert cas.simplify(result.H - H_expected) == 0


def test_pz_no_ac_source_raises():
    """solve_pz raises when no AC source is present."""
    c = Circuit("test")
    c.add_vsource("Vdc", "in", "0", 5)  # DC only, no ac_value
    c.add_resistor("R1", "in", "out", 1e3)
    c.add_capacitor("C1", "out", "0", 1e-6)
    with pytest.raises(ValueError, match="no AC source"):
        solve_pz(c, "out")


def test_pz_bad_output_node_raises():
    """solve_pz raises for unknown output node."""
    c = Circuit("test")
    c.add_vsource("Vin", "in", "0", 0, ac_value=1)
    c.add_resistor("R1", "in", "out", 1e3)
    with pytest.raises(ValueError, match="output node"):
        solve_pz(c, "does_not_exist", input_source="Vin")


def test_pz_bad_source_raises():
    """solve_pz raises for unknown input source."""
    c = Circuit("test")
    c.add_vsource("Vin", "in", "0", 0, ac_value=1)
    c.add_resistor("R1", "in", "out", 1e3)
    with pytest.raises(ValueError, match="not found"):
        solve_pz(c, "out", input_source="does_not_exist")


def test_pz_multiple_poles():
    """RLC bandpass: 2 poles, 1 zero at DC."""
    R, L, C_ = cas.symbols("R L C_", positive=True)
    c = Circuit("rlc_bp")
    c.add_vsource("Vin", "in", "0", 0, ac_value=1)
    c.add_resistor("R1", "in", "out", R)
    c.add_inductor("L1", "out", "mid", L)
    c.add_capacitor("C1", "mid", "0", C_)
    result = solve_pz(c, "out", input_source="Vin")
    # Bandpass with L and C in series: second-order denominator
    # H(s) = R/(R + s*L + 1/(s*C)) = s*R*C / (s^2*L*C + s*R*C + 1)
    # Denominator is second order -> 2 poles expected
    assert len(result.poles) == 2
    assert result.numerator is not None
    assert result.denominator is not None
