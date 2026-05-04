"""Voltage-controlled switch (smooth tanh model)."""
from sycan import cas as cas

from sycan import Circuit, solve_dc


def test_vswitch_closed_pulls_output_low():
    """V_c >> V_t (sharp on): the switch is roughly R_on across the load."""
    c = Circuit()
    c.add_vsource("V1", "in", "0", 1)
    c.add_vsource("Vctl", "ctl", "0", 5)  # well above threshold
    c.add_resistor("R1", "in", "out", 100)
    c.add_vswitch(
        "S1", "out", "0", "ctl", "0",
        R_on=1, R_off=1e9, V_t=2.5, V_h=0.05,
    )
    sol = solve_dc(c, simplify=False)
    V_out = float(sol[cas.Symbol("V(out)")])
    # With R_on ≈ 1 and R1 = 100 → divider gives ~ 1/101 V at the output.
    assert 0 < V_out < 0.05


def test_vswitch_open_pulls_output_high():
    """V_c << V_t (sharp off): the switch is ~ R_off."""
    c = Circuit()
    c.add_vsource("V1", "in", "0", 1)
    c.add_vsource("Vctl", "ctl", "0", 0)  # below threshold
    c.add_resistor("R1", "in", "out", 100)
    c.add_vswitch(
        "S1", "out", "0", "ctl", "0",
        R_on=1, R_off=1e9, V_t=2.5, V_h=0.05,
    )
    sol = solve_dc(c, simplify=False)
    V_out = float(sol[cas.Symbol("V(out)")])
    # Switch open: virtually no drop across R1 → V_out ≈ V_in.
    assert V_out > 0.99
