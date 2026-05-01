"""Output noise of a common-source NMOS amplifier (R_L load).

Both the load resistor and the MOSFET channel contribute. Setting
``lam = 0`` (no channel-length modulation) keeps the result compact:

    g_m              = mu_n · Cox · (W/L) · (V_GS_op - V_TH)
    H_R_L            = R_L                       (load → output)
    H_M (channel)    = R_L                       (drain → output)

so

    S_V_out = R_L² · ( 4·k_B·T/R_L  +  4·k_B·T·γ·g_m )
            = 4·k_B·T·R_L · (1 + γ·g_m·R_L)

with γ = 2/3 (long-channel saturation).
"""
from sycan import cas as cas

from sycan import Circuit, T_kelvin, autodraw, k_B, solve_noise
from sycan.components.active import NMOS_L1
from sycan.components.basic import Resistor, VoltageSource

mu_n, Cox, W, L = cas.symbols("mu_n Cox W L", positive=True)
V_TH, V_GS_op, V_DS_op = cas.symbols("V_TH V_GS_op V_DS_op", positive=True)
R_L, VDD = cas.symbols("R_L VDD", positive=True)

c = Circuit("cs amp noise")
c.add(VoltageSource("Vdd", "VDD", "0", value=VDD, ac_value=0))
c.add(VoltageSource("Vin", "gate", "0", value=V_GS_op, ac_value=0))
c.add(Resistor("RL", "VDD", "drain", R_L, include_noise="thermal"))
c.add(
    NMOS_L1(
        "M1", "drain", "gate", "0",
        mu_n=mu_n, Cox=Cox, W=W, L=L, V_TH=V_TH,
        V_GS_op=V_GS_op, V_DS_op=V_DS_op,
        include_noise="thermal",
    )
)

S_total, contribs = solve_noise(c, "drain", simplify=True)
g_m = mu_n * Cox * (W / L) * (V_GS_op - V_TH)
gamma = cas.Rational(2, 3)
expected = 4 * k_B * T_kelvin * R_L * (1 + gamma * g_m * R_L)

print("Per-source PSD contributions:")
for name, expr in contribs.items():
    print(f"  {name:>18}  =  {cas.simplify(expr)}")
print()
print(f"Total S_V_out      =  {cas.simplify(S_total)}")
print(f"Closed-form check  =  {cas.simplify(expected)}")
assert cas.simplify(S_total - expected) == 0

autodraw(c)
