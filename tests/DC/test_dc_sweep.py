"""DC parametric sweep over a symbolic source value."""
from sycan import cas as cas

from sycan import Circuit, solve_dc_sweep


def test_dc_sweep_voltage_divider():
    Vin = cas.Symbol("Vin")
    c = Circuit()
    c.add_vsource("V1", "in", "0", Vin)
    c.add_resistor("R1", "in", "mid", 1)
    c.add_resistor("R2", "mid", "0", 1)

    sweep = solve_dc_sweep(c, Vin, [1, 2, 3, 4], simplify=True)
    mid = cas.Symbol("V(mid)")

    assert len(sweep) == 4
    # Equal divider: V(mid) = Vin/2.
    assert cas.simplify(sweep[0][mid] - cas.Rational(1, 2)) == 0
    assert cas.simplify(sweep[1][mid] - 1) == 0
    assert cas.simplify(sweep[2][mid] - cas.Rational(3, 2)) == 0
    assert cas.simplify(sweep[3][mid] - 2) == 0


def test_dc_sweep_string_parameter_name():
    """Passing the parameter as a string should resolve to a Symbol."""
    c = Circuit()
    c.add_vsource("V1", "in", "0", "Vin")
    c.add_resistor("R1", "in", "out", "R")
    c.add_resistor("R2", "out", "0", "R")

    sweep = solve_dc_sweep(c, "Vin", [5, 10], simplify=True)
    out = cas.Symbol("V(out)")
    # Symbolic R cancels in a divider with two equal resistors.
    assert cas.simplify(sweep[0][out] - cas.Rational(5, 2)) == 0
    assert cas.simplify(sweep[1][out] - 5) == 0
