"""``solve_headroom`` — symbolic input range that keeps every MOSFET saturated.

The analysis is fully symbolic: it solves the saturation-form DC
operating point in closed form, builds per-device threshold /
overdrive predicates from it, and combines those predicates into a
sympy interval. So we test the *expressions* — not numeric edges.
"""
from __future__ import annotations

from sycan import cas as cas
import pytest

from sycan import Circuit, parse, solve_headroom
from sycan.headroom import HeadroomResult


# ---------------------------------------------------------------------------
# CS amp with resistor load (one MOSFET) — easy closed form.
#
#   I_D    = (1/2) β (V_in - V_TH)^2          (sat, lam = 0)
#   V_out  = V_DD - R_L · I_D                 (KCL through R_L)
#   sat    = V_in > V_TH  ∧  V_out >= V_in - V_TH
#
# The upper-edge predicate solves into V_in <= V_TH + (sqrt(1+2 β R_L V_DD) - 1)/(β R_L).
# ---------------------------------------------------------------------------
def test_resistor_load_cs_amp_yields_closed_form_interval():
    V_DD, V_THn, beta, R_L = cas.symbols("V_DD V_THn beta R_L", positive=True)
    c = Circuit()
    c.add_vsource("Vdd", "VDD", "0", V_DD)
    c.add_vsource("Vin", "in",  "0", 0)
    c.add_resistor("RL", "VDD", "out", R_L)
    c.add_nmos_l1(
        "MN", "out", "in", "0",
        mu_n=beta, Cox=1, W=1, L=1, V_TH=V_THn, lam=0,
    )

    r = solve_headroom(c, "Vin")
    assert isinstance(r, HeadroomResult)
    assert r.var == cas.Symbol("Vin", real=True)

    # Operating-point V(out) — quadratic in V_in.
    V_out = r.node_voltages[cas.Symbol("V(out)")]
    expected_Vout = V_DD - cas.Rational(1, 2) * R_L * beta * (cas.Symbol("Vin", real=True) - V_THn) ** 2
    assert cas.simplify(V_out - expected_Vout) == 0

    # Threshold predicate -> V_in - V_THn (must be > 0).
    c1, c2 = r.predicates["MN"]
    assert cas.simplify(c1 - (cas.Symbol("Vin", real=True) - V_THn)) == 0

    # Interval: lower edge = V_THn (threshold), upper edge has the
    # sqrt(1 + 2 β R_L V_DD) signature.
    assert r.interval is not None
    lo, hi = r.interval
    upper_expected = V_THn + (cas.sqrt(2 * R_L * V_DD * beta + 1) - 1) / (R_L * beta)
    assert cas.simplify(hi - upper_expected) == 0
    # The lower edge resolves to V_THn (the threshold), possibly wrapped
    # in a Max(...) — check that V_THn is one of the picks.
    assert lo == V_THn or (isinstance(lo, cas.Max) and V_THn in lo.args)


def test_circuit_is_not_mutated_by_solve_headroom():
    """Source values are restored, even when sources start as numbers."""
    netlist = """CS amp w/ R load
Vdd VDD 0 1.8
Vin in  0 0.7
RL  VDD out 1k
MN  out in 0   NMOS_L1 8e-4 1 1 1 0.45 0
.end
"""
    c = parse(netlist)
    vin = next(s for s in c.components if s.name == "Vin")
    before = vin.value
    solve_headroom(c, "Vin")
    assert vin.value == before


# ---------------------------------------------------------------------------
# Diff pair-style group sweep with one independent input variable.
# Tests that the dict form is accepted and that the input variable is
# inferred correctly.
# ---------------------------------------------------------------------------
def test_group_source_spec_with_inferred_var():
    V_DD, V_THn, beta, R_L, V_cm = cas.symbols(
        "V_DD V_THn beta R_L V_cm", positive=True
    )
    V_id = cas.Symbol("V_id", real=True)

    c = Circuit()
    c.add_vsource("Vdd",  "VDD", "0", V_DD)
    c.add_vsource("Vinp", "inp", "0", V_cm + V_id / 2)
    c.add_vsource("Vinm", "inm", "0", V_cm - V_id / 2)
    c.add_resistor("RLp", "VDD", "outp", R_L)
    c.add_resistor("RLm", "VDD", "outm", R_L)
    c.add_nmos_l1("M1", "outp", "inp", "0",
                  mu_n=beta, Cox=1, W=1, L=1, V_TH=V_THn, lam=0)
    c.add_nmos_l1("M2", "outm", "inm", "0",
                  mu_n=beta, Cox=1, W=1, L=1, V_TH=V_THn, lam=0)

    # Both V_cm and V_id appear in both source expressions, so the
    # caller must disambiguate which one is the swept variable. Pass
    # ``var=V_id`` and the analysis treats V_cm as a fixed parameter.
    r = solve_headroom(
        c,
        {"Vinp": V_cm + V_id / 2, "Vinm": V_cm - V_id / 2},
        var=V_id,
    )
    assert r.var == V_id

    # By symmetry the two devices contribute symmetric predicates: M1's
    # threshold becomes V_cm + V_id/2 - V_THn, M2's becomes
    # V_cm - V_id/2 - V_THn.
    c1_m1 = r.predicates["M1"][0]
    c1_m2 = r.predicates["M2"][0]
    assert cas.simplify(c1_m1 - (V_cm + V_id / 2 - V_THn)) == 0
    assert cas.simplify(c1_m2 - (V_cm - V_id / 2 - V_THn)) == 0


def test_group_source_spec_rejects_unknown_source():
    V_id = cas.Symbol("V_id", real=True)
    netlist = """one MOSFET
Vdd VDD 0 1.8
Vin in 0 0
RL VDD out 1k
MN out in 0 NMOS_L1 8e-4 1 1 1 0.45 0
.end
"""
    c = parse(netlist)
    with pytest.raises(ValueError, match="not found"):
        solve_headroom(c, {"Vmissing": V_id})


# ---------------------------------------------------------------------------
# Error / edge cases.
# ---------------------------------------------------------------------------
def test_no_mosfets_raises():
    c = parse("""resistor only
Vdd VDD 0 1.8
R1  VDD 0  1k
.end
""")
    with pytest.raises(ValueError, match="no MOSFETs"):
        solve_headroom(c, "Vdd")


def test_unknown_single_source_raises():
    c = parse("""one MOSFET
Vdd VDD 0 1.8
Vin in 0 0
RL VDD out 1k
MN out in 0 NMOS_L1 8e-4 1 1 1 0.45 0
.end
""")
    with pytest.raises(ValueError, match="not found"):
        solve_headroom(c, "Vmissing")


# ---------------------------------------------------------------------------
# op_point injection — for circuits cas.solve cannot close in one shot.
# The OTA-style flow: derive V(node)(x) yourself by sequential elimination,
# pass the dict in, and solve_headroom does the predicate / boundary phase
# entirely symbolically.
# ---------------------------------------------------------------------------
def test_op_point_injection_skips_sp_solve():
    V_DD, V_TH, beta, R_L = cas.symbols("V_DD V_TH beta R_L", positive=True)
    V_in = cas.Symbol("V_in", real=True)

    c = Circuit()
    c.add_vsource("Vdd", "VDD", "0", V_DD)
    c.add_vsource("Vin", "in",  "0", V_in)
    c.add_resistor("RL", "VDD", "out", R_L)
    c.add_nmos_l1("MN", "out", "in", "0",
                  mu_n=beta, Cox=1, W=1, L=1, V_TH=V_TH, lam=0)

    # Hand-derived operating point (V_out = V_DD − R_L · I_D_sat).
    op = {
        cas.Symbol("V(VDD)"): V_DD,
        cas.Symbol("V(in)"):  V_in,
        cas.Symbol("V(out)"): V_DD - cas.Rational(1, 2) * R_L * beta * (V_in - V_TH) ** 2,
    }
    r = solve_headroom(c, "Vin", var=V_in, op_point=op)

    # Predicates substitute the injected V(out), so MN.overdrive becomes
    # V_DD − (1/2) β R_L (V_in − V_TH)² + V_TH − V_in.
    c2 = r.predicates["MN"][1]
    expected = (V_DD - cas.Rational(1, 2) * R_L * beta * (V_in - V_TH) ** 2
                + V_TH - V_in)
    assert cas.simplify(c2 - expected) == 0

    # And the upper edge from solving c2 = 0 lands on the same closed
    # form as the no-op-point path.
    upper_expected = V_TH + (cas.sqrt(2 * R_L * V_DD * beta + 1) - 1) / (R_L * beta)
    assert r.interval is not None
    _, hi = r.interval
    assert cas.simplify(hi - upper_expected) == 0


def test_constant_only_dict_raises():
    """A dict whose every entry is a number has no input variable to sweep."""
    netlist = """CS amp
Vdd VDD 0 1.8
Vin in 0 0
RL VDD out 1k
MN out in 0 NMOS_L1 8e-4 1 1 1 0.45 0
.end
"""
    c = parse(netlist)
    with pytest.raises(ValueError, match="all source expressions are constants"):
        solve_headroom(c, {"Vin": cas.Rational(7, 10)})
