"""Linear small-signal model of a charge-pump phase-locked loop.

The classical 2nd-order Type-II PLL composed entirely of behavioural
``sycan.components.blocks`` elements:

    phi_ref --(+)--[K_d]--[F(s)]--[K_VCO/s]--+-- phi_out
              ^                              |
              |                              |
              +-------------- 1/N -----------+

with active proportional-integral loop filter

    F(s) = (1 + s τ_z) / (s τ_p)

Mapping each block onto a sycan primitive:

* phase-detector + ``K_d`` gain        →  ``Summer`` with weights (+K_d, -K_d/N)
* loop filter F(s)                     →  ``TransferFunction`` (PI controller)
* VCO  (V_ctrl → φ:  K_VCO / s)        →  ``Integrator``  (k = K_VCO)
* divider 1/N is folded into the summer weights
"""
import sympy as sp

from sycan import Circuit, autodraw, solve_ac

s = sp.Symbol("s")
phi_ref, K_d, K_VCO, tau_z, tau_p, N = sp.symbols(
    "phi_ref K_d K_VCO tau_z tau_p N", positive=True,
)

c = Circuit("Type-II charge-pump PLL")
c.add_vsource("V_ref", "ref", "0", value=0, ac_value=phi_ref)
c.add_summer(
    "PD", "pd", "0",
    inputs=[("ref", K_d), ("out", -K_d / N)],
)  # Phase Detector with gain K_d and feedback divider N
F = (1 + s * tau_z) / (s * tau_p)
c.add_transfer_function("F", "pd", "0", "ctrl", "0", H=F)   # PI Loop Filter
c.add_integrator("VCO", "ctrl", "0", "out", "0", k=K_VCO)   # VCO

sol = solve_ac(c)
H = sp.cancel(sp.simplify(sol[sp.Symbol("V(out)")] / phi_ref))

print(f"$$H(s) = \\frac{{\\phi_{{out}}(s)}}{{\\phi_{{ref}}(s)}} = {sp.latex(H)}$$")
print()
print("DC gain:")
print(f"    H(0) = {sp.simplify(H.subs(s, 0))}")
print()
print("(Type-II loop locks with zero static phase error → H(0) = N.)")

autodraw(c)
