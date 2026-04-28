"""NAND2 standby-leakage stacking effect — symbolic derivation.

Two NMOSs in series (the pull-down stack of a NAND2 with both inputs
at V_SS) leak less than a single NMOS would. The intermediate node
``mid`` floats up to a small positive voltage V_mid > 0, which has
three effects on the upper NMOS, MN_A:

  1. its V_GS goes *below* zero (V_GS_A = V_A − V_mid = -V_mid)
  2. its V_DS shrinks from V_DD to V_DD − V_mid
  3. its V_SB rises from 0 to V_mid, which (when bulk is tied to
     V_SS) raises the threshold by the body-effect formula
       V_TH(V_SB) = V_TH0 + γ · (√(2 φ_F + V_SB) − √(2 φ_F))

This example focuses on (3) — the body-effect contribution to
stacking — and does it *symbolically*, using only sympy expression
manipulation with one numeric substitution at the end. No Newton
iteration, no DC-operating-point solve.

The key result is the leakage-suppression ratio between the
"standard CMOS" (bulks → V_SS) and "well-isolated" (bulks → source)
configurations of the same NAND2, evaluated at the same V_SB:

      I_leak(bulk→V_SS)            ⎛   ΔV_TH(V_SB)  ⎞
      ─────────────────  =  exp ⎜ - ──────────── ⎟
      I_leak(bulk→source)          ⎝     m · V_T     ⎠

The schematic below renders the bulk→V_SS variant; MN_A's V_TH is
annotated with its body-effect-shifted value, MN_B's stays at V_TH0.
"""
import sympy as sp

from sycan import autodraw
from sycan.plot_util import fmt


# --- Symbols -------------------------------------------------------------
V_TH0, gamma, phi, V_SB, m_sub, V_T = sp.symbols(
    "V_TH0 gamma phi V_SB m V_T", positive=True,
)


# --- Body-effect threshold (the formula every textbook cites) -----------
V_TH_eff = V_TH0 + gamma * (sp.sqrt(phi + V_SB) - sp.sqrt(phi))
delta_V_TH = sp.simplify(V_TH_eff - V_TH0)

print(r"$$V_{TH}(V_{SB}) \;=\; " + sp.latex(V_TH_eff) + r"$$")
print()
print(r"$$\Delta V_{TH} \;=\; V_{TH}(V_{SB}) - V_{TH0} \;=\; "
      + sp.latex(delta_V_TH) + r"$$")
print()


# --- Leakage suppression ratio ------------------------------------------
# In weak inversion, the drain current is exponential in the
# overdrive: I_D ∝ exp((V_GS − V_TH) / (m·V_T)). For two NAND2
# variants at the *same* V_mid (i.e., the same V_GS_A = -V_mid for
# the upper NMOS), the only difference is V_TH_A — body-effect-
# raised in config A, frozen at V_TH0 in config B. Their ratio
# therefore collapses to a single exponential:
ratio = sp.exp(-delta_V_TH / (m_sub * V_T))
print(r"Leakage-suppression ratio (body effect alone):")
print(r"$$\frac{I_{leak}^{(a)}}{I_{leak}^{(b)}}"
      r" \;=\; \exp\!\left(-\frac{\Delta V_{TH}}{m\,V_T}\right)"
      r" \;=\; " + sp.latex(ratio) + r"$$")
print()


# --- Linearised body-effect coefficient (V_SB ≪ φ) ----------------------
# Taylor-expanding √(φ + V_SB) around V_SB = 0 gives ΔV_TH ≈ η · V_SB
# with η = γ / (2 √φ). In the standby state V_SB sits in the V_T
# range, so this linearisation is accurate to a few %.
eta = gamma / (2 * sp.sqrt(phi))
delta_V_TH_lin = sp.series(delta_V_TH, V_SB, 0, 2).removeO()
print(r"Small-V_SB expansion (V_SB \\ll \\phi):")
print(r"$$\Delta V_{TH} \;\approx\; \eta\, V_{SB}, \quad "
      r"\eta \;=\; " + sp.latex(eta) + r"$$")
print(f"   (verified: $\\Delta V_{{TH}}\\approx{sp.latex(delta_V_TH_lin)}$)")
print()


# --- Substitute typical 65-nm-ish numbers --------------------------------
PARAMS = {
    V_TH0:  sp.Rational(45, 100),    # 0.45 V
    gamma:  sp.Rational(40, 100),    # 0.4 V^(1/2)
    phi:    sp.Rational(70, 100),    # 0.7 V (= 2 φ_F)
    m_sub:  sp.Rational(3, 2),       # 1.5
    V_T:    sp.Rational(2585, 100000),  # 25.85 mV
}

# Typical V_mid in a stacked-NMOS standby state is one V_T or so
# (the intermediate node floats until the upper-NMOS V_GS is negative
# enough to balance the lower NMOS's V_DS-driven shape factor). We
# evaluate at V_SB = V_T and V_SB = 2 V_T to bracket the range.
print("--- numeric example, typical 65-nm-style parameters ---")
print(f"V_TH0 = {fmt(float(PARAMS[V_TH0]),  'V')},  "
      f"γ = {float(PARAMS[gamma]):.2f} √V,  "
      f"φ = {fmt(float(PARAMS[phi]), 'V')},  "
      f"m = {float(PARAMS[m_sub]):.2f},  "
      f"V_T = {fmt(float(PARAMS[V_T]), 'V')}")
print()
for V_SB_value in (PARAMS[V_T], 2 * PARAMS[V_T], 4 * PARAMS[V_T]):
    sub = {**PARAMS, V_SB: V_SB_value}
    V_TH_n   = float(V_TH_eff.subs(sub))
    delta_n  = float(delta_V_TH.subs(sub))
    eta_n    = float(eta.subs(sub))
    ratio_n  = float(ratio.subs(sub))
    print(f"V_SB = {fmt(float(V_SB_value), 'V')}:")
    print(f"   V_TH(MN_A)               = {fmt(V_TH_n, 'V')}    "
          f"(raised by ΔV_TH = {fmt(delta_n, 'V')}, η ≈ {eta_n:.3f})")
    print(f"   I_leak(a) / I_leak(b)    = {ratio_n:.4f}    "
          f"(body effect cuts standby leakage by {(1 - ratio_n) * 100:.2f}%)")


# --- Schematic with body-effect annotations -----------------------------
# Show the bulk-to-V_SS NAND2; we annotate MN_A with the V_TH it sees
# at V_SB = V_T (a representative standby V_mid) so the schematic
# reflects the symbolic result above.
V_DD_NUM   = 1.8
V_TH0_NUM  = float(PARAMS[V_TH0])
GAMMA_NUM  = float(PARAMS[gamma])
PHI_NUM    = float(PARAMS[phi])
M_NUM      = float(PARAMS[m_sub])
V_T_NUM    = float(PARAMS[V_T])
BETA_N     = 8.0e-4
BETA_P     = 4.0e-4

V_SB_typ   = V_T_NUM
V_TH_A_typ = float(V_TH_eff.subs({**PARAMS, V_SB: V_T_NUM}))

NETLIST = f"""NAND2 standby leakage (bulk→V_SS)
Vdd VDD 0 {V_DD_NUM}
VA  A   0 0
VB  B   0 0
MP_A out A VDD VDD PMOS_4T {BETA_P} 1 1 1 {V_TH0_NUM} 0 {GAMMA_NUM} {PHI_NUM} {M_NUM} {V_T_NUM}
MP_B out B VDD VDD PMOS_4T {BETA_P} 1 1 1 {V_TH0_NUM} 0 {GAMMA_NUM} {PHI_NUM} {M_NUM} {V_T_NUM}
MN_A out A mid 0   NMOS_4T {BETA_N} 1 1 1 {V_TH0_NUM} 0 {GAMMA_NUM} {PHI_NUM} {M_NUM} {V_T_NUM}
MN_B mid B 0   0   NMOS_4T {BETA_N} 1 1 1 {V_TH0_NUM} 0 {GAMMA_NUM} {PHI_NUM} {M_NUM} {V_T_NUM}
.end
"""

annotations = {
    "MN_A": [
        f"V_SB ≈ {fmt(V_SB_typ, 'V')}",
        f"V_TH = {fmt(V_TH_A_typ, 'V')}",
        f"(raised by Δ = {fmt(V_TH_A_typ - V_TH0_NUM, 'V')})",
    ],
    "MN_B": [
        "V_SB = 0",
        f"V_TH = {fmt(V_TH0_NUM, 'V')}",
    ],
    "Vdd": [f"V_DD = {fmt(V_DD_NUM, 'V')}"],
    "VA":  ["V_A  = 0"],
    "VB":  ["V_B  = 0"],
}

autodraw(NETLIST, back_annotation=annotations, seed=0)
