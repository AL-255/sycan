import sympy as sp
from sycan import Circuit, solve_impedance
from sycan import autodraw
# SRPP (Series-Regulated Push-Pull): two identical triodes stacked with
# a sense resistor Rs between T2's cathode and T1's plate.
K, mu, V_g_op, V_p_op, R_s, V_B = sp.symbols("K mu V_g_op V_p_op R_s V_B")

c = Circuit()
c.add_port("P_in",  "in",  "0", "input")
c.add_port("P_out", "out", "0", "output")
c.add_vsource("Vb", "hv", "0", value=V_B, ac_value=0)
c.add_triode("T1", plate="n_mid", grid="in",    cathode="0",
             K=K, mu=mu, V_g_op=V_g_op, V_p_op=V_p_op)
c.add_triode("T2", plate="hv",    grid="n_mid", cathode="out",
             K=K, mu=mu, V_g_op=V_g_op, V_p_op=V_p_op)
c.add_resistor("Rs", "out", "n_mid", R_s)

Z_out = solve_impedance(c, "P_out", termination="auto")
print("Z_out(R_s) =")
print(f"$$Z_{{out}}(R_s) = {sp.latex(sp.simplify(Z_out))}$$")
print()
print("R_s -> infinity limit (== R_L optimal for 2nd-harmonic cancellation):")
print(rf"$$\lim_{{R_s \to \infty}} Z_{{out}} = "
      f"{sp.latex(sp.simplify(sp.limit(Z_out, R_s, sp.oo)))}$$")

autodraw(c, res_dir=None)