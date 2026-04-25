import sympy as sp
from sycan import parse, solve_ac

netlist = """RC low-pass
V1 in 0 AC Vin
R1 in out R
C1 out 0 C
.end
"""

sol = solve_ac(parse(netlist))
Vin = sp.Symbol("Vin")
H = sol[sp.Symbol("V(out)")] / Vin
print(f"$$H(s) = {sp.latex(sp.simplify(H))}$$")
