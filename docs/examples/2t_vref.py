import sympy as sp
from sycan import parse
from sycan.components.active import NMOS_subthreshold
from sycan import autodraw

# Classical 2-transistor sub-threshold voltage reference:
# sub-threshold currents in two cross-connected NMOS devices with
# different geometry / thresholds pin V(n1) to a CTAT-cancelling
# log-of-ratio voltage.
netlist = """2T reference
V1 VDD 0 VDD
M1 VDD 0 n1 NMOS_subthreshold mu_n1 Cox1 W1 L1 V_TH1 m1 V_T
M2 n1 n1 0 NMOS_subthreshold mu_n2 Cox2 W2 L2 V_TH2 m2 V_T
.end
"""
circuit = parse(netlist)
mosfets = [c for c in circuit.components if isinstance(c, NMOS_subthreshold)]
print(f"Parsed {len(circuit.components)} components, "
      f"including {len(mosfets)} sub-threshold NMOS devices.")

# sp.solve can't close the transcendental KCL directly. But in the
# saturation limit (V_DS >> V_T) matching I_D1 = I_D2 reduces to a
# log-space linear equation, with closed-form solution:
mu_n1, mu_n2, Cox1, Cox2 = sp.symbols(
    "mu_n1 mu_n2 Cox1 Cox2", positive=True)
W1, W2, L1, L2 = sp.symbols("W1 W2 L1 L2", positive=True)
V_TH1, V_TH2, m1, m2, V_T = sp.symbols(
    "V_TH1 V_TH2 m1 m2 V_T", positive=True)

V_n1 = (m1*m2/(m1+m2) * (V_TH2 - V_TH1)
        + m1*m2/(m1+m2) * V_T * sp.log(
            mu_n1*Cox1*W1*L2 / (mu_n2*Cox2*W2*L1)))
print()
print(f"$$V(n_1) = {sp.latex(sp.simplify(V_n1))}$$")

# Temperature compensation: V_T = k_B*T/q is the symbol most directly tied
# to T (it is exactly proportional to absolute temperature). Expanding V_T
# and differentiating gives the dV(n_1)/dT = 0 condition that flattens the
# reference over T.
T, k_B, q = sp.symbols("T k_B q", positive=True)
V_n1_of_T = V_n1.subs(V_T, k_B*T/q)
dVdT = sp.simplify(sp.diff(V_n1_of_T, T))
print()
print("Setting dV(n_1)/dT = 0 gives the compensation condition:")
print(rf"$$\frac{{dV(n_1)}}{{dT}} = {sp.latex(dVdT)} = 0$$")


autodraw(netlist, res_dir=None)