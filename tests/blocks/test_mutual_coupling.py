"""Mutual inductance coupling tests (SPICE ``K``)."""
from sycan import cas as cas

from sycan import Circuit, solve_ac, solve_dc


def test_mutual_coupling_series_perfect():
    """Two perfectly coupled inductors in series. k=1 doubles the
    effective inductance: L_eff = L1 + L2 + 2*M = 4L (equal L)."""
    L_val = cas.Symbol("L", positive=True)
    c = Circuit("series")
    c.add_vsource("Vin", "in", "0", value=0, ac_value=1)
    c.add_inductor("L1", "in", "mid", L_val)
    c.add_inductor("L2", "mid", "0", L_val)
    c.add_mutual_coupling("K1", ["L1", "L2"], k=1)
    sol = solve_ac(c)
    s = cas.Symbol("s")

    # I = 1 / (s * (L + L + 2*L)) = 1 / (4*s*L)
    expected_I = 1 / (4 * s * L_val)
    assert cas.simplify(sol[cas.Symbol("I(L1)")] - expected_I) == 0
    assert cas.simplify(sol[cas.Symbol("I(L2)")] - expected_I) == 0
    # V(mid) = 1/2 (equal L, perfect coupling → symmetric divider)
    assert cas.simplify(sol[cas.Symbol("V(mid)")] - cas.Rational(1, 2)) == 0


def test_mutual_coupling_series_k_half():
    """Two coupled inductors with k=1/2."""
    L_val = cas.Symbol("L", positive=True)
    c = Circuit("series_k05")
    c.add_vsource("Vin", "in", "0", value=0, ac_value=1)
    c.add_inductor("L1", "in", "mid", L_val)
    c.add_inductor("L2", "mid", "0", L_val)
    c.add_mutual_coupling("K1", ["L1", "L2"], k=cas.Rational(1, 2))
    sol = solve_ac(c)
    s = cas.Symbol("s")

    # M = k*sqrt(L*L) = L/2,  L_eff = 2L + 2*(L/2) = 3L
    expected_I = 1 / (3 * s * L_val)
    assert cas.simplify(sol[cas.Symbol("I(L1)")] - expected_I) == 0
    # V(mid) = I * (s*L + s*M) = 1/(3sL) * 3sL/2 = 1/2
    assert cas.simplify(sol[cas.Symbol("V(mid)")] - cas.Rational(1, 2)) == 0


def test_mutual_coupling_three_inductors():
    """Three coupled inductors in a transformer-like configuration."""
    L_val = cas.Symbol("L", positive=True)
    R_val = cas.Symbol("R", positive=True)
    c = Circuit("three")
    c.add_vsource("Vin", "in", "0", value=0, ac_value=1)
    c.add_inductor("L1", "in", "0", L_val)
    c.add_inductor("L2", "out1", "0", L_val)
    c.add_inductor("L3", "out2", "0", L_val)
    c.add_resistor("R1", "out1", "0", R_val)
    c.add_resistor("R2", "out2", "0", R_val)
    c.add_mutual_coupling("K1", ["L1", "L2", "L3"], k=1)
    sol = solve_ac(c)
    assert cas.Symbol("V(out1)") in sol
    assert cas.Symbol("V(out2)") in sol
    assert cas.Symbol("I(L1)") in sol


def test_mutual_coupling_dc_no_effect():
    """DC: coupling has no effect (inductors are shorts)."""
    c = Circuit("dc_k")
    c.add_vsource("Vin", "in", "0", 5)
    c.add_resistor("R1", "in", "mid", 1000)
    c.add_inductor("L1", "mid", "0", cas.Rational(1, 1000))
    c.add_resistor("R2", "mid", "out", 2000)
    c.add_inductor("L2", "out", "0", cas.Rational(1, 1000))
    c.add_mutual_coupling("K1", ["L1", "L2"], k=1)
    sol = solve_dc(c)
    # DC: inductors are shorts → V(mid) = V(out) = 0
    assert cas.simplify(sol[cas.Symbol("V(in)")] - 5) == 0
    assert cas.simplify(sol[cas.Symbol("V(mid)")]) == 0


def test_mutual_coupling_unequal_L():
    """Two coupled inductors with different L values."""
    L1_val, L2_val = cas.symbols("L1 L2", positive=True)
    c = Circuit("unequal")
    c.add_vsource("Vin", "in", "0", value=0, ac_value=1)
    c.add_inductor("L1", "in", "mid", L1_val)
    c.add_inductor("L2", "mid", "0", L2_val)
    c.add_mutual_coupling("K1", ["L1", "L2"], k=1)
    sol = solve_ac(c)
    s = cas.Symbol("s")

    # M = sqrt(L1*L2), L_eff = L1 + L2 + 2*M
    M = cas.sqrt(L1_val * L2_val)
    L_eff = L1_val + L2_val + 2 * M
    expected_I = 1 / (s * L_eff)
    assert cas.simplify(sol[cas.Symbol("I(L1)")] - expected_I) == 0


def test_mutual_coupling_spice_netlist():
    """Parse a SPICE netlist with a K element."""
    from sycan import parse, solve_ac

    netlist = """coupled coils
V1 in 0 AC 1; down
L1 in mid 1; right
L2 mid 0 1; right
K1 L1 L2 1; right
.end"""
    sol = solve_ac(parse(netlist))
    assert cas.simplify(sol[cas.Symbol("V(mid)")] - cas.Rational(1, 2)) == 0


def test_mutual_coupling_spice_k_before_l():
    """SPICE: K can be defined before the inductors it references."""
    from sycan import parse, solve_ac

    netlist = """forward ref
V1 in 0 AC 1; down
K1 L1 L2 1
L1 in mid 1; right
L2 mid 0 1; right
.end"""
    sol = solve_ac(parse(netlist))
    assert cas.simplify(sol[cas.Symbol("V(mid)")] - cas.Rational(1, 2)) == 0


def test_mutual_coupling_construction():
    """Python API construction."""
    from sycan.components.basic import MutualCoupling, Inductor

    kc = MutualCoupling("K9", k=cas.Rational(4, 5))
    kc.couple("LX")
    kc.couple("LY")
    kc.resolve([Inductor("LX", "a", "b", 1e-3), Inductor("LY", "c", "d", 2e-3)])
    assert kc._values["LX"] == 1e-3
    assert kc._values["LY"] == 2e-3


def test_mutual_coupling_missing_inductor_raises():
    """resolve() raises ValueError if an inductor is missing."""
    import pytest
    from sycan.components.basic import MutualCoupling, Inductor

    kc = MutualCoupling("K9", k=cas.Rational(4, 5))
    kc.couple("LX")
    kc.couple("MISSING")
    with pytest.raises(ValueError, match="MISSING"):
        kc.resolve([Inductor("LX", "a", "b", 1e-3)])
