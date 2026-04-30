"""Second-order continuous-time sigma-delta modulator (CIFB).

Builds the canonical cascade-of-integrators-feed-back loop entirely
out of behavioural ``sycan.components.blocks`` primitives:

    V_in --(+)--> [1/s] --(+)--> [1/s] --[Q]--+--> V_out
            ^             ^                   |
            |             |                   |
            +-------------+-------------------+   (unit-gain feedback)

Solving the loop symbolically with ``solve_ac`` extracts the
signal-transfer-function (STF) and noise-transfer-function (NTF) by
reading the coefficients of V_in and V_q in the closed-form output.
"""
import sympy as sp

from sycan import Circuit, autodraw, solve_ac

c = Circuit("2nd-order CT sigma-delta")
Vin = sp.Symbol("Vin")
c.add_vsource("V1", "in", "0", value=0, ac_value=Vin)

# Stage 1: error summer (input minus feedback) followed by integrator.
c.add_summer("S1", "e1", "0", inputs=[("in", 1), ("out", -1)])
c.add_integrator("I1", "e1", "0", "x1", "0", k=1)

# Stage 2: identical error summer + integrator.
c.add_summer("S2", "e2", "0", inputs=[("x1", 1), ("out", -1)])
c.add_integrator("I2", "e2", "0", "x2", "0", k=1)

# 1-bit quantizer modelled as unity gain plus additive symbol V_q_Q1.
c.add_quantizer("Q1", "x2", "0", "out", "0", k_q=1)

s = sp.Symbol("s")
sol = solve_ac(c)

V_out = sp.expand(sp.together(sol[sp.Symbol("V(out)")]))
V_q = sp.Symbol("V_q_Q1")

STF = sp.simplify(V_out.coeff(Vin))
NTF = sp.simplify(V_out.coeff(V_q))

print(f"$$\\mathrm{{STF}}(s) = {sp.latex(STF)}$$")
print(f"$$\\mathrm{{NTF}}(s) = {sp.latex(NTF)}$$")
print()
print(f"NTF zeros at s = 0:  NTF(0) = {sp.simplify(NTF.subs(s, 0))}")
print(f"                    NTF'(0) = {sp.simplify(sp.diff(NTF, s).subs(s, 0))}")
print()
print("(Double zero at DC: 40 dB/decade of in-band noise rejection.)")

autodraw(c)
