from sycan import cas as cas
from sycan import parse, solve_dc
from sycan import autodraw

netlist = """wheatstone bridge
V1 a 0 Vs
R1 a b R1
R2 b 0 R2
R3 a c R3
R4 c 0 R4
.end
"""

sol = solve_dc(parse(netlist))
V_b = sol[cas.Symbol("V(b)")]
V_c = sol[cas.Symbol("V(c)")]
diff = cas.simplify(V_b - V_c)
print(f"$$V(b) - V(c) = {cas.latex(diff)}$$")
print()
print("Balance condition: R1*R4 == R2*R3")

autodraw(netlist)