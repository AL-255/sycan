import sympy as sp
from sycan import parse, solve_dc
from sycan import autodraw
netlist = """voltage divider
V1 in 0 Vin
R1 in out Ra
R2 out 0 Rb
.end
"""

for sym, expr in solve_dc(parse(netlist)).items():
    print(f"$${sp.latex(sym)} = {sp.latex(expr)}$$")

autodraw(netlist, res_dir=None)