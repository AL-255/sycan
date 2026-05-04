from sycan import cas as cas
from sycan import Circuit, solve_ac, autodraw

# Symbolic N-JFET common-source amplifier
# BETA, VTO, LAMBDA, Rd, Rs, VDD are symbolic component values
# V_GS_op, V_DS_op are the DC operating point (supplied by user or DC solver)
# C_gs, C_gd are intrinsic gate capacitances for AC
s = cas.Symbol("s")
BETA, VTO, LAMBDA, Rd, Rs, VDD = cas.symbols("BETA VTO LAMBDA Rd Rs VDD", positive=True)
V_GS_op, V_DS_op, C_gs, C_gd = cas.symbols("V_GS_op V_DS_op C_gs C_gd", positive=True)

c = Circuit("NJFET CS Amplifier")
c.add_vsource("Vin", "gate", "0", 0, ac_value=1)
c.add_vsource("Vdd", "VDD", "0", VDD, ac_value=0)
c.add_resistor("Rd", "VDD", "drain", Rd)
c.add_resistor("Rs", "source", "0", Rs)
c.add_njfet(
    "J1", "drain", "gate", "source",
    BETA, VTO,
    LAMBDA=LAMBDA,
    C_gs=C_gs, C_gd=C_gd,
    V_GS_op=V_GS_op, V_DS_op=V_DS_op,
)

sol = solve_ac(c)
H = sol[cas.Symbol("V(drain)")]

print("=== N-JFET Common-Source Amplifier ===\n")

# Mid-band gain (s -> 0, ignore capacitances)
H0 = cas.simplify(H.subs(s, 0).subs({C_gs: 0, C_gd: 0}))
print(f"$$A_v(s=0) = {cas.latex(H0)}$$")

# g_m and g_ds at the operating point
gm = cas.diff(
    BETA * (V_GS_op + VTO)**2 * (1 + LAMBDA * V_DS_op),
    V_GS_op,
)
gds = cas.diff(
    BETA * (V_GS_op + VTO)**2 * (1 + LAMBDA * V_DS_op),
    V_DS_op,
)
gm_s = cas.simplify(gm)
gds_s = cas.simplify(gds)
print()
print(f"$$g_m = \\frac{{\\partial I_D}}{{\\partial V_{{GS}}}} = {cas.latex(gm_s)}$$")
print(f"$$g_{{ds}} = \\frac{{\\partial I_D}}{{\\partial V_{{DS}}}} = {cas.latex(gds_s)}$$")

# Source-degeneration insight
print()
print("With source degeneration (finite R_s), the effective transconductance is")
print(f"$$G_m = \\frac{{g_m}}{{1 + g_m R_s + g_{{ds}}(R_s + R_d)}}$$")
print("and the mid-band gain approaches $$A_v \\approx -G_m R_d$$.")

# Show the fully-symbolic transfer function
print()
print("Full transfer function (Laplace domain):")
print(f"$$H(s) = {cas.latex(cas.simplify(H))}$$")

autodraw(c, seed=0)
