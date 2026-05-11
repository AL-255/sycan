"""``Region`` assumption — post-solve operating-point verification.

Region assumptions don't transform the equations. After solving, the
checker evaluates whether each device actually landed in the claimed
region (saturation, triode, cutoff for MOSFETs; forward-active,
saturation, cutoff, reverse-active for BJTs).
"""
import pytest

from sycan import (
    Circuit,
    Region,
    cas,
    check_assumptions,
    format_check_report,
    solve_dc,
    violations,
)


# ---------------------------------------------------------------------------
# MOSFET regions
# ---------------------------------------------------------------------------

def _cs_amp(V_in: cas.Expr, V_TH: cas.Expr = cas.Rational(1, 2)) -> Circuit:
    c = Circuit("cs_amp")
    c.add_vsource("Vdd", "VDD", "0", cas.Rational(9, 5))   # 1.8 V
    c.add_vsource("Vin", "g", "0", V_in)
    c.add_resistor("RL", "VDD", "d", 10000)
    c.add_nmos_l1(
        "M1", "d", "g", "0",
        cas.Rational(1, 1000), cas.Rational(1, 500),
        10, 1, V_TH,
    )
    return c


def test_mosfet_saturation_check_passes_for_high_RL():
    c = _cs_amp(V_in=cas.Rational(7, 10))   # V_GS = 0.7, V_TH = 0.5
    sol = solve_dc(c)
    results = check_assumptions(c, sol, [Region("M1", "saturation")])
    assert all(r.passed for r in results), format_check_report(results)


def test_mosfet_cutoff_check_detects_cutoff():
    c = _cs_amp(V_in=cas.Rational(3, 10))   # V_GS = 0.3 < V_TH = 0.5
    sol = solve_dc(c)

    # Wrongly asserting saturation must fail and report the violating
    # inequality (V_GS_eff ≤ V_TH).
    sat_results = check_assumptions(c, sol, [Region("M1", "saturation")])
    failed = violations(sat_results)
    assert len(failed) == 1
    assert "cutoff" in failed[0].detail
    assert "V_GS_eff" in failed[0].detail

    # Correctly asserting cutoff must pass.
    cutoff_results = check_assumptions(c, sol, [Region("M1", "cutoff")])
    assert all(r.passed for r in cutoff_results)


def test_mosfet_triode_check_detects_low_v_ds():
    """Push V_DS below V_OV by raising RL until the device leaves saturation."""
    c = Circuit("triode_amp")
    c.add_vsource("Vdd", "VDD", "0", cas.Rational(9, 5))
    c.add_vsource("Vin", "g", "0", cas.Rational(17, 10))   # V_GS=1.7, V_OV=1.2
    c.add_resistor("RL", "VDD", "d", 100000)               # large → V_DS pulled low
    c.add_nmos_l1(
        "M1", "d", "g", "0",
        cas.Rational(1, 1000), cas.Rational(1, 500),
        10, 1, cas.Rational(1, 2),
    )
    sol = solve_dc(c)

    # Saturation should fail (device actually in triode after the drop).
    sat = check_assumptions(c, sol, [Region("M1", "saturation")])
    assert not all(r.passed for r in sat)
    detail = violations(sat)[0].detail
    assert "triode" in detail


def test_region_check_unknown_component_fails_loudly():
    c = _cs_amp(V_in=cas.Rational(7, 10))
    sol = solve_dc(c)
    results = check_assumptions(c, sol, [Region("M99", "saturation")])
    assert len(results) == 1
    assert not results[0].passed
    assert "not found" in results[0].detail


def test_region_check_unknown_region_fails_with_valid_list():
    c = _cs_amp(V_in=cas.Rational(7, 10))
    sol = solve_dc(c)
    results = check_assumptions(c, sol, [Region("M1", "tornado")])
    assert not results[0].passed
    assert "valid:" in results[0].detail


def test_check_report_format_renders_pass_and_fail_lines():
    c = _cs_amp(V_in=cas.Rational(3, 10))   # cutoff
    sol = solve_dc(c)
    results = check_assumptions(
        c, sol,
        [Region("M1", "cutoff"), Region("M1", "saturation")],
    )
    text = format_check_report(results)
    assert "[OK  ] M1 in cutoff" in text
    assert "[FAIL] M1 in saturation" in text


# ---------------------------------------------------------------------------
# BJT regions
#
# The Gummel-Poon BJT model contributes transcendental ``exp(V/V_T)``
# residuals that cause ``cas.solve`` to spin on circuits without a
# tightly pinned operating point. The region checker, however, is pure
# linear-algebra on the solution dict — exercise it against a
# hand-rolled solution that simulates each operating region.
# ---------------------------------------------------------------------------

def _bjt_only_circuit(polarity: str = "NPN") -> Circuit:
    c = Circuit(f"{polarity.lower()}_only")
    c.add_bjt("Q1", "c", "b", "e", polarity,
              cas.Rational(1, 10**14), 100, 1)
    return c


def _fake_node_solution(**voltages) -> dict:
    return {cas.Symbol(f"V({n})"): cas.sympify(v)
            for n, v in voltages.items()}


def test_bjt_forward_active_passes_for_npn_in_normal_bias():
    c = _bjt_only_circuit("NPN")
    # V_BE = 0.7 > 0, V_BC = 0.7 - 3 = -2.3 < 0  → forward-active.
    sol = _fake_node_solution(c=3, b=cas.Rational(7, 10), e=0)
    results = check_assumptions(c, sol, [Region("Q1", "forward-active")])
    assert results[0].passed, results[0].detail


def test_bjt_forward_active_fails_when_collector_pulled_low():
    c = _bjt_only_circuit("NPN")
    # V_BC = 0.7 - 0.2 = +0.5 → both junctions forward → saturation.
    sol = _fake_node_solution(c=cas.Rational(2, 10),
                              b=cas.Rational(7, 10), e=0)
    results = check_assumptions(c, sol, [Region("Q1", "forward-active")])
    assert not results[0].passed
    assert "V_BC_eff" in results[0].detail


def test_bjt_saturation_check_passes_when_both_junctions_forward():
    c = _bjt_only_circuit("NPN")
    sol = _fake_node_solution(c=cas.Rational(1, 10),
                              b=cas.Rational(7, 10), e=0)
    results = check_assumptions(c, sol, [Region("Q1", "saturation")])
    assert results[0].passed, results[0].detail


def test_bjt_cutoff_check_passes_when_both_junctions_off():
    c = _bjt_only_circuit("NPN")
    sol = _fake_node_solution(c=3, b=0, e=0)
    results = check_assumptions(c, sol, [Region("Q1", "cutoff")])
    assert results[0].passed, results[0].detail


def test_bjt_pnp_polarity_inverts_signs():
    """PNP forward-active: V_EB > 0 and V_BC > 0  ⇔  V_BE_eff > 0 and
    V_BC_eff < 0 after the polarity sign-flip."""
    c = _bjt_only_circuit("PNP")
    # PNP active: V_E > V_B > V_C.  V_BE = -0.7 → V_BE_eff = 0.7;
    # V_BC = 2.3 → V_BC_eff = -2.3.
    sol = _fake_node_solution(c=0, b=cas.Rational(23, 10), e=3)
    results = check_assumptions(c, sol, [Region("Q1", "forward-active")])
    assert results[0].passed, results[0].detail


def test_bjt_unknown_region_reports_valid_options():
    c = _bjt_only_circuit("NPN")
    sol = _fake_node_solution(c=3, b=cas.Rational(7, 10), e=0)
    results = check_assumptions(c, sol, [Region("Q1", "amplifying")])
    assert not results[0].passed
    assert "valid:" in results[0].detail
