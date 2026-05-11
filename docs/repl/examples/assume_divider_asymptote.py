"""Voltage divider: simplify by asserting one resistor is much smaller
than the other.

The exact V(out) of a divider is ``Vin · R2/(R1 + R2)``. Real designs
often have one resistor *much* larger than the other — and the
assumption engine's ``MuchGreater`` lets us write that down directly.
With ``R1 >> R2`` the output collapses to zero (load is shorted to
ground through the small resistor); with ``R2 >> R1`` it collapses
to ``Vin``.
"""
from sycan import cas as cas
from sycan import Circuit, MuchGreater, autodraw, solve

Vin, R1, R2 = cas.symbols("Vin R1 R2", positive=True)

c = Circuit("divider")
c.add_vsource("V1", "in", "0", Vin)
c.add_resistor("R1", "in", "out", R1)
c.add_resistor("R2", "out", "0", R2)

V_out = cas.Symbol("V(out)")

exact = cas.simplify(solve(c, mode="dc")[V_out])
print("Exact V(out):")
print(f"$$V_{{out}} = {cas.latex(exact)}$$")
print()

big_R1 = solve(c, mode="dc", assume=[MuchGreater(R1, R2)], simplify=True)[V_out]
big_R2 = solve(c, mode="dc", assume=[MuchGreater(R2, R1)], simplify=True)[V_out]

print("If R1 >> R2 (output mostly grounded):")
print(f"$$V_{{out}} \\to {cas.latex(big_R1)}$$")
print()
print("If R2 >> R1 (output sees the source directly):")
print(f"$$V_{{out}} \\to {cas.latex(big_R2)}$$")

autodraw(c)
