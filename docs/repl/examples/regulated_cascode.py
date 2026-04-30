import sympy as sp

from sycan import Circuit, autodraw, solve_headroom

# Regulated (gain-boosted) cascode current source.

V_TH, V_OV, beta, V_bias, A_gain, V_DD = sp.symbols(
    "V_TH V_OV beta V_bias A V_DD", positive=True
)
V_out = sp.Symbol("V_out", real=True)
I_in = sp.Rational(1, 2) * beta * V_OV ** 2

V_n3 = V_TH + V_OV
V_d2 = V_bias
V_g1 = V_bias + V_TH + V_OV

# --- Build the netlist ---------------------------------------------------
c = Circuit()
c.add_vsource("Vbias", "Vbias", "0",  V_bias)
c.add_vsource("Vdd",   "VDD",   "0",  V_DD)
c.add_isource("I_in",  "VDD",     "n3", I_in)
c.add_vsource("Vout",  "out",   "0",  V_out)

# Op-amp modeled as an ideal high-gain VCVS
c.add_vcvs("OpAmp", "g1", "0", "Vbias", "d2", A_gain)

c.add_nmos_l1("Q3", "n3",  "n3", "0",  mu_n=beta, Cox=1, W=1, L=1, V_TH=V_TH, lam=0)
c.add_nmos_l1("Q2", "d2",  "n3", "0",  mu_n=beta, Cox=1, W=1, L=1, V_TH=V_TH, lam=0)
c.add_nmos_l1("Q1", "out", "g1", "d2", mu_n=beta, Cox=1, W=1, L=1, V_TH=V_TH, lam=0)

r = solve_headroom(c, "Vout", var=V_out)

# --- Symbolic output -----------------------------------------------------
lo, hi = r.interval
print("Output headroom — every NMOS stays in saturation when:")
print(rf"$$V(\text{{out}}) \in \left[\;{sp.latex(sp.simplify(lo))}\;,"
      rf"\;{sp.latex(sp.simplify(hi))}\;\right]$$")
print(r"With $V_{bias} = V_{OV}$ (Q2 just at its saturation knee), the "
      r"output node settles to the wide-swing minimum "
      r"$V(\text{out})_{\min} = 2 V_{OV}$.")

autodraw(c)