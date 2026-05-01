from sycan import cas as cas
from sycan import Circuit, solve_impedance
from sycan import autodraw
mu_n, Cox, W, L, V_TH, lam, R_L = cas.symbols("mu_n Cox W L V_TH lam R_L")
VDD, V_GS_op, V_DS_op, C_gs = cas.symbols("VDD V_GS_op V_DS_op C_gs")

c = Circuit()
c.add_port("P_in",  "gate",  "0", "input")
c.add_port("P_out", "drain", "0", "output")
c.add_vsource("Vdd", "VDD", "0", value=VDD, ac_value=0)
c.add_resistor("RL", "VDD", "drain", R_L)
c.add_nmos_l1("M1", "drain", "gate", "0",
              mu_n=mu_n, Cox=Cox, W=W, L=L, V_TH=V_TH, lam=lam,
              C_gs=C_gs, V_GS_op=V_GS_op, V_DS_op=V_DS_op)

Z_in  = cas.simplify(solve_impedance(c, "P_in",  termination="auto"))
Z_out = cas.simplify(solve_impedance(c, "P_out", termination="auto"))
print("Z_in  = 1/(s C_gs):")
print(f"$$Z_{{in}} = {cas.latex(Z_in)}$$")
print()
print("Z_out = R_L || r_o:")
print(f"$$Z_{{out}} = {cas.latex(Z_out)}$$")

autodraw(c)