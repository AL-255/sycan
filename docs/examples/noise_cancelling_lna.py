"""Bruccoleri/Klumperink CG–CS noise-cancelling LNA.

Two-output NMOS amplifier:

    M1 (common-gate)   gate=VBIAS, source=vin,   drain=voutp
    M2 (common-source) gate=vin,   source=0,     drain=voutn

The signal V_sig drives ``vs`` through R_S; the DC-blocking cap C_IN
ties ``vp_in`` to ``vin`` at the operating frequency, so for the AC
analysis below it's modelled as an ideal wire (a 0 V voltage source).
The DC bias current I_DC is open at AC.

The thermal noise of M1 generates a current ``i_n`` that flows
drain → source. Externally it perturbs both ``voutp`` (directly) and
``vin`` (through M1's source); the latter is then re-amplified onto
``voutn`` by M2. The differential output ``V_diff = V(voutp) − V(voutn)``
sees the same M1 noise on both legs and they can cancel.

The example uses ideal ``VCCS`` transconductors for the small-signal
behaviour of M1 and M2 so the symbolic answer comes out clean — the
trick that *defines* this LNA topology is independent of the device
model. (Replace VCCS with NMOS_L1 and the same condition falls out,
just buried inside the long-channel g_m expression.)
"""
import sympy as sp

from sycan import Circuit, solve_ac
from sycan.components.basic import (
    CurrentSource, Resistor, VCCS, VCVS, VoltageSource,
)

# --- Symbols -----------------------------------------------------------
RS_, R1_, R2_, IDC_ = sp.symbols("RS R1 R2 IDC", positive=True)
VDD_v, VBIAS_v = sp.symbols("VDD VBIAS", positive=True)
gm1, gm2 = sp.symbols("g_m1 g_m2", positive=True)


def build(*, v_sig_ac: sp.Expr = 0, probe: str | None = None) -> Circuit:
    """Construct the LNA. ``v_sig_ac`` drives V_SIG; ``probe`` injects a
    1 A AC current source mimicking one of the noise sources."""
    c = Circuit("noise-cancelling LNA")
    # Bias and signal sources (all AC-zero except V_SIG / probe).
    c.add(VoltageSource("VDD",   "VDD", "0", VDD_v,   ac_value=0))
    c.add(VoltageSource("VBIAS", "vg1", "0", VBIAS_v, ac_value=0))
    c.add(VoltageSource("VSIG",  "vs",  "0", 0,       ac_value=v_sig_ac))
    c.add(Resistor("RS",  "vs",    "vp_in", RS_))
    # CIN is a DC block; at the operating frequency it acts as an AC
    # short. Model it as a 0 V voltage source ("ideal wire") so the
    # solver doesn't carry a dead Laplace pole around.
    c.add(VoltageSource("Wcin", "vp_in", "vin", 0, ac_value=0))
    c.add(CurrentSource("IDC", "vin", "0", IDC_, ac_value=0))
    c.add(Resistor("R1", "VDD", "voutp", R1_))
    c.add(Resistor("R2", "VDD", "voutn", R2_))
    # Small-signal MOSFETs as VCCS transconductors.
    c.add(VCCS("M1", "voutp", "vin", "vg1", "vin", gm1))   # gm1·(V_g − V_s)
    c.add(VCCS("M2", "voutn", "0",   "vin", "0",   gm2))   # gm2·V_in
    # Differential output extracted via a unit-gain VCVS.
    c.add(VCVS("Ediff", "vdiff", "0", "voutp", "voutn", 1))
    # Optional 1 A AC noise probe in the same direction as each
    # device's intrinsic noise current.
    probes = {
        "M1": ("voutp", "vin"),     # M1 channel:  drain → source
        "M2": ("voutn", "0"),       # M2 channel:  drain → source
        "R1": ("VDD", "voutp"),     # across R1
        "R2": ("VDD", "voutn"),     # across R2
    }
    if probe:
        np_, nm_ = probes[probe]
        c.add(CurrentSource("Probe", np_, nm_, 0, ac_value=1))
    return c


def H(probe: str) -> sp.Expr:
    """Trans-impedance from a unit noise-current at ``probe`` to V_diff."""
    sol = solve_ac(build(probe=probe))
    return sp.factor(sp.simplify(sol[sp.Symbol("V(vdiff)")]))


# --- 1. Differential signal gain --------------------------------------
H_sig = sp.factor(sp.simplify(
    solve_ac(build(v_sig_ac=1))[sp.Symbol("V(vdiff)")]
))
print(f"V(vdiff) / V_sig  =  {H_sig}\n")

# --- 2. Per-source noise transimpedances ------------------------------
H_M1 = H("M1")
H_M2 = H("M2")
H_R1 = H("R1")
H_R2 = H("R2")
print(f"H_M1 (channel noise → V_diff)  =  {H_M1}")
print(f"H_M2                           =  {H_M2}")
print(f"H_R1                           =  {H_R1}")
print(f"H_R2                           =  {H_R2}\n")

# --- 3. Cancellation condition for M1 ---------------------------------
numer = sp.factor(sp.together(H_M1).as_numer_denom()[0])
print(f"H_M1 numerator (must vanish):   {numer}")
cond = sp.solve(numer, R2_)
assert cond, "no cancellation solution found"
R2_canc = sp.simplify(cond[0])
print(f"Cancellation condition:         R2 = {R2_canc}")
print(f"Equivalently:                   g_m2 · R2 · RS = R1\n")

# --- 4. Sanity-check — at the cancellation R2, M1 vanishes; the rest persist.
H_M1_at_cond = sp.simplify(H_M1.subs(R2_, R2_canc))
H_sig_at_cond = sp.simplify(H_sig.subs(R2_, R2_canc))
print(f"H_M1  @ R2 = R1/(g_m2·RS):  {H_M1_at_cond}")
print(f"signal gain @ same R2:       {sp.factor(H_sig_at_cond)}")
print("\nM1 thermal noise cancels in V_diff while the *signal* still")
print("propagates through both M1 and M2 — that is the noise-canceller's trick.")
