"""Region assumptions: catch a biasing mistake before it propagates.

A common bug in hand-analysis is to assume a MOSFET is in saturation
without checking. The assumption engine's :class:`Region` claim is a
no-op on the equations themselves, but :meth:`Circuit.check_assumptions`
re-evaluates each claim against the solved operating point and reports
any device that landed somewhere other than where you said.

Below: the *same* common-source amplifier, biased two ways. With a
healthy V_GS the device is in saturation; with V_GS pulled below V_TH
the same circuit lives in cutoff — and the saturation assumption
flags the violating inequality.
"""
from sycan import cas as cas
from sycan import (
    Circuit,
    Region,
    autodraw,
    check_assumptions,
    format_check_report,
    solve_dc,
)


def cs_amp(v_in_value: cas.Expr) -> Circuit:
    c = Circuit("cs_amp")
    c.add_vsource("Vdd", "VDD", "0", cas.Rational(9, 5))   # 1.8 V
    c.add_vsource("Vin", "g", "0", v_in_value)
    c.add_resistor("RL", "VDD", "d", 10000)
    c.add_nmos_l1(
        "M1", "d", "g", "0",
        cas.Rational(1, 1000), cas.Rational(1, 500),
        10, 1, cas.Rational(1, 2),
    )
    c.assume(Region("M1", "saturation"))
    return c


# Healthy bias: V_GS = 0.7 V, well above V_TH = 0.5 V.
good = cs_amp(cas.Rational(7, 10))
sol_good = solve_dc(good)
print("Healthy bias (V_in = 0.7 V):")
print(f"$$V_d = {cas.latex(sol_good[cas.Symbol('V(d)')])}$$")
print(format_check_report(check_assumptions(good, sol_good)))
print()

# Bad bias: V_GS pulled below V_TH — should land in cutoff, not saturation.
bad = cs_amp(cas.Rational(3, 10))
sol_bad = solve_dc(bad)
print("Under-biased (V_in = 0.3 V):")
print(f"$$V_d = {cas.latex(sol_bad[cas.Symbol('V(d)')])}$$")
print(format_check_report(check_assumptions(bad, sol_bad)))

autodraw(good)
