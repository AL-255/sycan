"""Symbolic sensitivity analysis on a resistive divider."""
from sycan import cas as cas

from sycan import Circuit, solve_sensitivity


def test_sensitivity_voltage_divider():
    Vin, Ra, Rb = cas.symbols("Vin Ra Rb", positive=True)
    c = Circuit()
    c.add_vsource("V1", "in", "0", Vin)
    c.add_resistor("Ra", "in", "mid", Ra)
    c.add_resistor("Rb", "mid", "0", Rb)

    s = solve_sensitivity(c, "mid", parameters=[Vin, Ra, Rb], simplify=True)
    # V_mid = Rb · Vin / (Ra + Rb)
    # ∂V_mid/∂Vin = Rb / (Ra + Rb)
    expected_vin = Rb / (Ra + Rb)
    assert cas.simplify(s[Vin] - expected_vin) == 0
    # ∂V_mid/∂Ra = -Rb·Vin / (Ra+Rb)^2
    expected_ra = -Rb * Vin / (Ra + Rb) ** 2
    assert cas.simplify(s[Ra] - expected_ra) == 0
    # ∂V_mid/∂Rb = Ra·Vin / (Ra+Rb)^2
    expected_rb = Ra * Vin / (Ra + Rb) ** 2
    assert cas.simplify(s[Rb] - expected_rb) == 0


def test_sensitivity_normalized():
    Vin, Ra, Rb = cas.symbols("Vin Ra Rb", positive=True)
    c = Circuit()
    c.add_vsource("V1", "in", "0", Vin)
    c.add_resistor("Ra", "in", "mid", Ra)
    c.add_resistor("Rb", "mid", "0", Rb)

    s = solve_sensitivity(
        c, "mid", parameters=[Vin], normalized=True, simplify=True,
    )
    # Normalised w.r.t. Vin should be unity (V_mid is linear in Vin).
    assert cas.simplify(s[Vin] - 1) == 0
