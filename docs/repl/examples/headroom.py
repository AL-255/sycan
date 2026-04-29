import sympy as sp

from sycan import Circuit, autodraw, solve_headroom
from sycan.plot_util import fmt

# Finding the valid input interval of a CS amp with a resistor load. 
# The input has to be greater than the NMOS threshold voltage,
# while the output has to be greater than VOV to keep the NMOS in saturation.
V_DD, V_TH, beta, R_L = sp.symbols("V_DD V_TH beta R_L", positive=True)
V_in = sp.Symbol("V_in", real=True)

c = Circuit()
c.add_vsource("Vdd", "VDD", "0", V_DD)
c.add_vsource("Vin", "in",  "0", V_in)
c.add_resistor("RL", "VDD", "out", R_L)
c.add_nmos_l1(
    "MN", "out", "in", "0",
    mu_n=beta, Cox=1, W=1, L=1, V_TH=V_TH, lam=0,
)

r = solve_headroom(c, "Vin", var=V_in) # Solve Headroom Analysis

# --- Symbolic results ---------------------------------------------------
V_out = sp.simplify(r.node_voltages[sp.Symbol("V(out)")])
print("Operating point — V_out solved with the saturation drain current only:")
print(rf"$$V_{{out}}(V_{{in}}) = {sp.latex(V_out)}$$")
print()

print("Per-MOSFET saturation predicates (each must be ≥ 0):")
for name, (c1, c2) in r.predicates.items():
    print(rf"$${name}\;\;\text{{threshold}}: {sp.latex(c1)} \;>\; 0$$")
    print(rf"$${name}\;\;\text{{overdrive}}: {sp.latex(c2)} \;\geq\; 0$$")
print()

lo, hi = r.interval
print("Headroom interval (closed form):")
print(rf"$$V_{{in}} \in \left[\;{sp.latex(sp.simplify(lo))}\;,\;{sp.latex(sp.simplify(hi))}\;\right]$$")

autodraw(c)
