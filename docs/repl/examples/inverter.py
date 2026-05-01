from sycan import cas as cas

from sycan import autodraw, parse, solve_dc
from sycan.plot_util import fmt

# --- Device parameters ---------------------------------------------------
V_DD    = 1.8
V_TH_N  = 0.45
V_TH_P  = 0.45
BETA_N  = 8.0e-4
BETA_P  = 4.0e-4
LAMBDA  = 0.0
M_SUB   = 1.5      # sub-threshold slope factor
V_T     = 0.02585  # thermal voltage at ~300 K

# --- Read V_in from the page slider (or fall back) -----------------------
V_in = V_DD / 2.0
try:
    import js
    V_in = float(js.document.getElementById("inverter-vin").value)
except Exception:
    pass

# --- Schematic + DC operating point --------------------------------------
NETLIST = f"""CMOS inverter
Vdd VDD 0 {V_DD}
Vin in  0 {V_in:.6g}
MN  out in 0   NMOS_3T {BETA_N} 1 1 1 {V_TH_N} {LAMBDA} {M_SUB} {V_T}
MP  out in VDD PMOS_3T {BETA_P} 1 1 1 {V_TH_P} {LAMBDA} {M_SUB} {V_T}
.end
"""

c = parse(NETLIST)
sol = solve_dc(c, simplify=False)
V_out = float(sol[cas.Symbol("V(out)")])

# Pull the parsed MOSFETs back out so we can ask each device about its
# region / drain current at the operating point we just solved for.
mn = next(d for d in c.components if d.name == "MN")
mp = next(d for d in c.components if d.name == "MP")
V_GS_n, V_DS_n = V_in, V_out
V_GS_p, V_DS_p = V_in - V_DD, V_out - V_DD
reg_n = mn.operating_region(V_GS_n, V_DS_n)
reg_p = mp.operating_region(V_GS_p, V_DS_p)
I_D_n = mn.dc_current(V_GS_n, V_DS_n)
I_D_p = mp.dc_current(V_GS_p, V_DS_p)

print(f"V_in   = {fmt(V_in,  'V')}")
print(f"V_out  = {fmt(V_out, 'V')}")
print(f"MN: {reg_n:>14}   V_GS={fmt(V_GS_n, 'V', sign=True)}  "
      f"V_DS={fmt(V_DS_n, 'V', sign=True)}  I_D={fmt(I_D_n, 'A', sign=True)}")
print(f"MP: {reg_p:>14}   V_GS={fmt(V_GS_p, 'V', sign=True)}  "
      f"V_DS={fmt(V_DS_p, 'V', sign=True)}  I_D={fmt(I_D_p, 'A', sign=True)}")

annotations = {
    "MN":  [f"region: {reg_n}",
            f"ID  = {fmt(I_D_n,  'A', sign=True)}"],
    "MP":  [f"region: {reg_p}",
            f"ID  = {fmt(I_D_p,  'A', sign=True)}"],
    "Vin": [f"V_in  = {fmt(V_in,  'V')}"],
    "Vdd": [f"V_DD  = {fmt(V_DD,  'V')}", f"V_out = {fmt(V_out, 'V')}"],
}

autodraw(NETLIST, back_annotation=annotations, seed=0)
