"""Transfer-function solver smoke tests on RC low-pass / high-pass."""
from sycan import cas as cas

from sycan import Circuit, solve_tf


def test_tf_rc_lowpass_dc_gain_is_one():
    R, C = cas.symbols("R C", positive=True)
    c = Circuit()
    c.add_vsource("V1", "in", "0", 0, ac_value=1)
    c.add_resistor("R1", "in", "out", R)
    c.add_capacitor("C1", "out", "0", C)

    tf = solve_tf(c, "out", input_source="V1")
    s = cas.Symbol("s")
    H_expected = 1 / (1 + s * R * C)
    assert cas.simplify(tf["H"] - H_expected) == 0
    # H(0) = 1 (DC gain through to the cap divider).
    assert cas.simplify(tf["dc_gain"] - 1) == 0


def test_tf_rc_highpass_dc_gain_is_zero():
    R, C = cas.symbols("R C", positive=True)
    c = Circuit()
    c.add_vsource("V1", "in", "0", 0, ac_value=1)
    c.add_capacitor("C1", "in", "out", C)
    c.add_resistor("R1", "out", "0", R)

    tf = solve_tf(c, "out", input_source="V1", simplify=True)
    s = cas.Symbol("s")
    # Standard high-pass: H(s) = sRC / (1 + sRC), DC gain is 0.
    assert cas.simplify(tf["dc_gain"]) == 0
    # As s → ∞, the cap is a short → H → 1.
    assert cas.simplify(tf["hf_gain"] - 1) == 0
