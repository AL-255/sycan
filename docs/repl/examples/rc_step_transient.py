from sycan import cas as cas
from sycan import Circuit, solve_transient
from sycan import autodraw

# Symbolic transient analysis: RC low-pass driven by a voltage step.
# solve_transient() solves the Laplace-domain MNA system, then
# inverse-Laplace-transforms the result into exact time-domain
# expressions.

R, C, Vstep = cas.symbols("R C Vstep", positive=True)

c = Circuit("rc_step")
c.add_vsource("V1", "in", "0", 0,
              waveform="pulse", v1=0, v2=Vstep, td=0, pw=cas.oo)
c.add_resistor("R1", "in", "out", R)
c.add_capacitor("C1", "out", "0", C)

# Outputs can be node names ("out" -> V(out)) or branch-current
# symbols like I(V1).
tran = solve_transient(
    c, outputs=["out", cas.Symbol("I(V1)")], simplify=True
)

vout_s = tran.s_solution[cas.Symbol("V(out)")]
vout_t = tran.t_solution[cas.Symbol("V(out)")]
print("Laplace domain:")
print(f"$$V_{{out}}(s) = {cas.latex(cas.simplify(vout_s))}$$")
print("Time domain (classic 1 - e^(-t/RC) charging curve):")
print(f"$$v_{{out}}(t) = {cas.latex(vout_t)}$$")

# The branch current through the source decays as the cap charges.
i_t = tran.t_solution[cas.Symbol("I(V1)")]
print(f"$$i_{{V1}}(t) = {cas.latex(i_t)}$$")

autodraw(c)
