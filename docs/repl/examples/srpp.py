from sycan import cas as cas
from sycan import Circuit, solve_impedance
from sycan import autodraw
# SRPP (Series-Regulated Push-Pull): two identical triodes stacked with
# a sense resistor Rs between T2's cathode and T1's plate.
K, mu, V_g_op, V_p_op, R_s, V_B = cas.symbols("K mu V_g_op V_p_op R_s V_B")

c = Circuit()
c.add_port("P_in",  "in",  "0", "input")
c.add_port("P_out", "out", "0", "output")
c.add_vsource("Vb", "VDD", "0", value=V_B, ac_value=0)
c.add_triode("T1", plate="n_mid", grid="in",    cathode="0",
             K=K, mu=mu, V_g_op=V_g_op, V_p_op=V_p_op)
c.add_triode("T2", plate="VDD",    grid="n_mid", cathode="out",
             K=K, mu=mu, V_g_op=V_g_op, V_p_op=V_p_op)
c.add_resistor("Rs", "out", "n_mid", R_s)

Z_out = solve_impedance(c, "P_out", termination="auto")
print("Z_out(R_s) =")
print(f"$$Z_{{out}}(R_s) = {cas.latex(cas.simplify(Z_out))}$$")
print()
print("R_s -> infinity limit (== R_L optimal for 2nd-harmonic cancellation):")
print(rf"$$\lim_{{R_s \to \infty}} Z_{{out}} = "
      f"{cas.latex(cas.simplify(cas.limit(Z_out, R_s, cas.oo)))}$$")

autodraw(c)