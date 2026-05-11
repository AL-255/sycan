"""Inverting amplifier: collapse the closed-loop expression to its ideal
form by asserting that the op-amp's open-loop gain is infinite.

The exact gain of a real op-amp in feedback is a messy rational
function of ``A``, ``Ri``, and ``Rf``. Once we declare ``A → ∞``,
sycan's assumption engine folds the limit through the solution and the
ideal closed-loop gain ``-Rf/Ri`` falls right out.
"""
import sympy

from sycan import cas as cas
from sycan import Circuit, Limit, autodraw, solve

Vin, Ri, Rf = cas.symbols("Vin Ri Rf", positive=True)

c = Circuit("inv_amp")
c.add_vsource("V1", "in", "0", Vin)
c.add_resistor("Ri", "in", "inv", Ri)
c.add_resistor("Rf", "out", "inv", Rf)
U1 = c.add_opamp("U1", "0", "inv", "out")     # 'A' is U1.A
c.add_resistor("Rl", "out", "0", 1000)

V_out = cas.Symbol("V(out)")

# Without any assumption: full closed-loop expression in (A, Ri, Rf).
sol_finite = solve(c, mode="dc", simplify=True)
print("Finite-gain V(out):")
print(f"$$V_{{out}} = {cas.latex(sol_finite[V_out])}$$")
print()

# Attach 'A → ∞' and solve again.
sol_ideal = solve(c, mode="dc", assume=[Limit(U1.A, sympy.oo)], simplify=True)
gain_ideal = cas.simplify(sol_ideal[V_out] / Vin)
print("After assuming A → ∞:")
print(f"$$\\frac{{V_{{out}}}}{{V_{{in}}}} = {cas.latex(gain_ideal)}$$")

autodraw(c)
