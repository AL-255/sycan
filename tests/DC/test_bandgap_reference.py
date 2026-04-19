"""Bandgap reference: PTAT (ΔV_BE) + CTAT (V_BE) summation.

A classical bandgap reference outputs

    V_REF = V_BE + K * ΔV_BE

where ``V_BE = V_T * ln(I_bias/IS + 1)`` is CTAT (roughly
``-2 mV/K`` in silicon) and ``ΔV_BE = V_BE1 - V_BE2 = V_T * ln(N)``
(for ``IS2 = N·IS1``) is PTAT (``+0.086 mV/K`` per unit of
``ln(N)``). Choosing ``K`` to satisfy ``d V_REF / dT = 0`` lands
``V_REF`` near the silicon bandgap energy (~1.22 V).

The test circuit below realises the PTAT core: two diode-connected
devices (represented by our :class:`~sycan.Diode` Shockley model)
driven by equal currents but with saturation currents ``IS1`` and
``IS2`` encoding the emitter-area ratio. The test then composes
``V_REF = V(a) + K*(V(a) - V(b))`` and verifies the closed-form
expression analytically.
"""
import sympy as sp

from sycan import parse, solve_dc

NETLIST = """bandgap PTAT/CTAT generator
I1 0 a I_bias
D1 a 0 IS1 1 V_T
I2 0 b I_bias
D2 b 0 IS2 1 V_T
.end
"""


def test_bandgap_diode_voltages():
    I_bias, IS1, IS2, V_T = sp.symbols("I_bias IS1 IS2 V_T")
    sol = solve_dc(parse(NETLIST))
    # KCL at each diode node pins I_bias = IS_i*(exp(V/V_T) - 1), so
    # V = V_T * log(I_bias/IS_i + 1) in closed form.
    V_a_expected = V_T * sp.log(I_bias / IS1 + 1)
    V_b_expected = V_T * sp.log(I_bias / IS2 + 1)
    assert sp.simplify(sol[sp.Symbol("V(a)")] - V_a_expected) == 0
    assert sp.simplify(sol[sp.Symbol("V(b)")] - V_b_expected) == 0


def test_bandgap_ptat_delta_vbe():
    """ΔV_BE = V_T * ln(((I_bias+IS1)·IS2)/((I_bias+IS2)·IS1)), which
    reduces to V_T*ln(IS2/IS1) = V_T*ln(N) in the saturation limit."""
    I_bias, IS1, IS2, V_T = sp.symbols("I_bias IS1 IS2 V_T")
    sol = solve_dc(parse(NETLIST))
    delta = sol[sp.Symbol("V(a)")] - sol[sp.Symbol("V(b)")]
    expected = V_T * sp.log((I_bias + IS1) * IS2 / ((I_bias + IS2) * IS1))
    assert sp.simplify(sp.expand_log(delta - expected, force=True)) == 0


def test_bandgap_output_is_linear_combination():
    """The bandgap-summation step V_REF = V_BE1 + K·ΔV_BE is purely
    linear and carries the CTAT + PTAT pieces the previous two tests
    verified symbolically."""
    I_bias, IS1, IS2, V_T, K = sp.symbols("I_bias IS1 IS2 V_T K")
    sol = solve_dc(parse(NETLIST))
    V_BE1 = sol[sp.Symbol("V(a)")]
    V_BE2 = sol[sp.Symbol("V(b)")]
    V_REF = V_BE1 + K * (V_BE1 - V_BE2)

    # V_REF is linear in K with V_BE1 as the K=0 intercept and
    # ΔV_BE = V_BE1 - V_BE2 as the slope in K.
    assert sp.simplify(V_REF.subs(K, 0) - V_BE1) == 0
    dVREF_dK = sp.diff(V_REF, K)
    assert sp.simplify(dVREF_dK - (V_BE1 - V_BE2)) == 0
