"""Output-voltage noise PSD of an RC low-pass filter.

The series resistor's Johnson-Nyquist current noise (one-sided PSD
``S_I = 4·k_B·T / R``) flows through the load capacitor; the pole at
``ω = 1 / (R·C)`` shapes the output voltage spectrum into the famous
single-pole roll-off

    S_V_out(ω) = 4·k_B·T·R / (1 + (ω·R·C)**2)

whose total integrated power is the celebrated ``k_B·T / C``.
"""
import sympy as sp

from sycan import Circuit, T_kelvin, autodraw, k_B, solve_noise
from sycan.components.basic import Capacitor, Resistor, VoltageSource

R, C, omega = sp.symbols("R C omega", positive=True)

c = Circuit("RC noise demo")
c.add(VoltageSource("V1", "in", "0", value=0, ac_value=0))
c.add(Resistor("R1", "in", "out", R, include_noise="thermal"))
c.add(Capacitor("C1", "out", "0", C))

s = sp.Symbol("s")
S_total, contribs = solve_noise(c, "out", s=s, simplify=True)

# Express the PSD on the imaginary axis s = jω.
S_omega = sp.simplify(S_total.subs(s, sp.I * omega))
print(f"S_V_out(ω) = {sp.latex(S_omega)}")

# Sanity-check: integrating over Hz (dω = 2π df) gives kT/C.
power = sp.integrate(S_omega, (omega, 0, sp.oo)) / (2 * sp.pi)
print(f"Integrated noise power: {sp.simplify(power)}")
assert sp.simplify(power - k_B * T_kelvin / C) == 0

autodraw(c)
