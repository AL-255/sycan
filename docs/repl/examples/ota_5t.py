import sympy as sp

from sycan import Circuit, autodraw, solve_headroom

# --- Input mode -----------------------------------------------------------
# Toggle between fully differential drive and single-ended drive:
#   "differential"  → Vinp = V_MID + x, Vinn = V_MID − x   (two-source bias)
#   "single_ended"  → Vinp = V_MID + x, Vinn = V_MID       (one input swings)
# The whole symbolic chain below specialises to the chosen mode.
INPUT_MODE = "differential"   # "differential" | "single_ended"

# 5T-OTA: NMOS input pair (M1, M2), PMOS current-mirror load
# (M3 diode-connected, M4 mirroring), NMOS tail current source (M5).
# x is the one independent variable for solve_headroom.
#
# Simplifications:
#   - λ = 0 (no channel-length modulation)
#   - M1 == M2: NMOS input pair sized W_n / L_n
#   - M3 == M4: PMOS mirror sized W_p / L_p
#   - M5     : NMOS tail current source sized W_c / L_c
#
# With λ = 0 the KCL at the output is degenerate (V_out is set by the
# external load), so the headroom interval is bounded by the input-pair
# thresholds V_OV1 = V_OV2 = 0 — not by anything happening at ``out``.

# --- Symbolic device parameters ------------------------------------------
V_DD, V_TH, mu_n, mu_p, Cox, V_bn, V_MID = sp.symbols(
    "V_DD V_TH mu_n mu_p Cox V_bn V_MID", positive=True)
W_n, L_n, W_p, L_p, W_c, L_c = sp.symbols("W_n L_n W_p L_p W_c L_c", positive=True)
x = sp.Symbol("x", real=True)

# Aspect-ratio betas:
#     β_n = μ_n Cox (W_n/L_n)   — input pair (M1, M2)
#     β_p = μ_p Cox (W_p/L_p)   — current mirror (M3, M4)
#     β_c = μ_n Cox (W_c/L_c)   — tail current source (M5)
beta_n = mu_n * Cox * W_n / L_n
beta_p = mu_p * Cox * W_p / L_p
beta_c = mu_n * Cox * W_c / L_c

# --- Input-mode-specific source values + V_tail closed form --------------
if INPUT_MODE == "differential":
    V_inp = V_MID + x
    V_inn = V_MID - x
    # Tail KCL with V_OV1 = s + x, V_OV2 = s − x:
    #     2 s² + 2 x² = (β_c / β_n) V_OV5²    →    s = √((β_c/2β_n) V_OV5² − x²)
    # Save as ``s_def`` / ``s_sq_def`` for back-substitution further down.
    s = sp.Symbol("s", positive=True)
    s_sq_def = beta_c / (2 * beta_n) * (V_bn - V_TH) ** 2 - x ** 2
elif INPUT_MODE == "single_ended":
    V_inp = V_MID + x
    V_inn = V_MID
    # Tail KCL with V_OV1 = s + x, V_OV2 = s:
    #     2 s² + 2 s x + x² = (β_c / β_n) V_OV5²
    # Solving the quadratic in s (positive root):
    #     s = (−x + √(2 (β_c/β_n) V_OV5² − x²)) / 2
    s = sp.Symbol("s", positive=True)
    s_sq_def = None  # tail-KCL is a quadratic in s; see the back-substitution below.
else:
    raise ValueError(f"INPUT_MODE must be 'differential' or 'single_ended'")

V_tail = V_MID - V_TH - s
V_OV1  = V_inp - V_tail - V_TH        # generic — works in both modes
V_OV2  = V_inn - V_tail - V_TH

# n3 KCL (mirror diode connection on M3):  β_n V_OV1² = β_p (V_DD − V_n3 − V_TH)²
V_n3 = V_DD - V_TH - sp.sqrt(beta_n / beta_p) * V_OV1

# With λ = 0 the output KCL is degenerate; V(out) is a free symbol set
# by the external load.
V_out = sp.Symbol("V_out", real=True)

# --- Build the netlist ---------------------------------------------------
c = Circuit()
c.add_vsource("Vdd",  "VDD", "0", V_DD)
c.add_vsource("Vbn",  "Vbn", "0", V_bn)
c.add_vsource("Vinp", "inp", "0", V_inp)
c.add_vsource("Vinn", "inn", "0", V_inn)
c.add_nmos_l1("M1", "n3",  "inp", "tail", mu_n=mu_n, Cox=Cox, W=W_n, L=L_n, V_TH=V_TH, lam=0)
c.add_nmos_l1("M2", "out", "inn", "tail", mu_n=mu_n, Cox=Cox, W=W_n, L=L_n, V_TH=V_TH, lam=0)
c.add_pmos_l1("M3", "n3",  "n3",  "VDD",  mu_n=mu_p, Cox=Cox, W=W_p, L=L_p, V_TH=V_TH, lam=0)
c.add_pmos_l1("M4", "out", "n3",  "VDD",  mu_n=mu_p, Cox=Cox, W=W_p, L=L_p, V_TH=V_TH, lam=0)
c.add_nmos_l1("M5", "tail", "Vbn", "0",   mu_n=mu_n, Cox=Cox, W=W_c, L=L_c, V_TH=V_TH, lam=0)

# --- Hand-derived operating point fed into solve_headroom ----------------
op = {
    sp.Symbol("V(VDD)"):  V_DD,
    sp.Symbol("V(Vbn)"):  V_bn,
    sp.Symbol("V(inp)"):  V_inp,
    sp.Symbol("V(inn)"):  V_inn,
    sp.Symbol("V(tail)"): V_tail,
    sp.Symbol("V(n3)"):   V_n3,
    sp.Symbol("V(out)"):  V_out,
}

r = solve_headroom(
    c,
    sources={"Vinp": V_inp, "Vinn": V_inn},
    var=x,
    op_point=op,
    simplify=True,
)

# --- Symbolic output ------------------------------------------------------
print(f"Input mode: **{INPUT_MODE}**  "
      f"(Vinp = {sp.latex(V_inp)}, Vinn = {sp.latex(V_inn)})")
print()
print(r"Operating point — sequential elimination on the L1 saturation"
      r" form with $\lambda = 0$, using the auxiliary symbol "
      r"$s = V_{MID} - V_{TH} - V_{\text{tail}}$:")
print(rf"$$V_{{\text{{tail}}}} = V_{{MID}} - V_{{TH}} - s$$")
print(rf"$$V_{{n_3}} = V_{{DD}} - V_{{TH}} - \sqrt{{\beta_n / \beta_p}}\,(s + x)$$")
if INPUT_MODE == "differential":
    print(r"With the constraint $s^2 + x^2 = "
          r"\dfrac{\beta_c}{2 \beta_n}\,(V_{bn} - V_{TH})^2$.")
else:
    print(r"With the constraint $2 s^2 + 2 s x + x^2 = "
          r"\dfrac{\beta_c}{\beta_n}\,(V_{bn} - V_{TH})^2$.")
print()

print("Per-MOSFET saturation predicates (each must be ≥ 0):")
for name, (c1, c2) in r.predicates.items():
    print(rf"$${name}\;\;\text{{threshold}}: {sp.latex(sp.simplify(c1))} \;>\; 0$$")
    print(rf"$${name}\;\;\text{{overdrive}}: {sp.latex(sp.simplify(c2))} \;\geq\; 0$$")
print()

# --- Close the input-side interval ---------------------------------------
# The binding edges are V_OV1 = 0 (M1 turns off) and V_OV2 = 0 (M2 turns
# off). Closing each predicate against the tail-KCL constraint gives:
#
#   differential: V_OV1·V_OV2 = s² − x² = (β_c/2β_n)V_OV5² − 2x²
#                                                 →  |x|_max = V_OV5/2 · √(β_c/β_n)
#   single-ended: M2.threshold = s > 0 dominates; with s = (−x + √…)/2 > 0
#                                                 →  |x|_max = V_OV5     · √(β_c/β_n)
if INPUT_MODE == "differential":
    x_max = (V_bn - V_TH) / 2 * sp.sqrt(W_c * L_n / (W_n * L_c))
    formula_latex = (
        r"\dfrac{V_{bn}-V_{TH}}{2}\,\sqrt{\dfrac{W_c\,L_n}{W_n\,L_c}}"
        r"\;=\;\dfrac{V_{OV5}}{2}\,\sqrt{\dfrac{\beta_c}{\beta_n}}"
    )
    # Sanity-check: x_max² is a root of V_OV1 · V_OV2 = 0 with s² folded in.
    combined = (V_OV1 * V_OV2).subs(s ** 2, s_sq_def)
    assert sp.simplify(combined.subs(x, x_max)) == 0
else:
    x_max = (V_bn - V_TH) * sp.sqrt(W_c * L_n / (W_n * L_c))
    formula_latex = (
        r"(V_{bn}-V_{TH})\,\sqrt{\dfrac{W_c\,L_n}{W_n\,L_c}}"
        r"\;=\;V_{OV5}\,\sqrt{\dfrac{\beta_c}{\beta_n}}"
    )
    # Sanity-check: at x = x_max, s = 0 satisfies the tail-KCL constraint
    # 2 s² + 2 s x + x² = (β_c/β_n) V_OV5² and zeros V_OV2 = s.
    constraint = 2 * 0 ** 2 + 2 * 0 * x_max + x_max ** 2 \
        - beta_c / beta_n * (V_bn - V_TH) ** 2
    assert sp.simplify(constraint) == 0

print(f"Input range that keeps every transistor in saturation"
      f"  ({INPUT_MODE} drive):")
print(rf"$$x \in \left[\;-{sp.latex(x_max)}\;,\;{sp.latex(x_max)}\;\right]$$")
print(rf"$$|x|_{{\max}} \;=\; {formula_latex}$$")
print("  binding: M1.threshold (low) and M2.threshold (high)")

autodraw(c)
