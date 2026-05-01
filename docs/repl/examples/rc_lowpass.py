from sycan import cas as cas
from sycan import parse, solve_ac
from sycan import autodraw
netlist = """RC low-pass
V1 in 0 AC Vin
R1 in out R
C1 out 0 C
.end
"""

sol = solve_ac(parse(netlist))
Vin = cas.Symbol("Vin")
H = sol[cas.Symbol("V(out)")] / Vin
print(f"$$H(s) = {cas.latex(cas.simplify(H))}$$")

autodraw(netlist)