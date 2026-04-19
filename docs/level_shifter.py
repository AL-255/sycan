"""Bistable level-shifter Monte-Carlo demo.

Netlist::

    MP0 VDD OUT_P OUT_N       ; PMOS, S=VDD, G=OUT_P, D=OUT_N
    MP1 VDD OUT_N OUT_P       ; PMOS, S=VDD, G=OUT_N, D=OUT_P
    MN0 0   IN_P  OUT_N       ; NMOS, S=GND, G=IN_P,  D=OUT_N
    MN1 0   IN_N  OUT_P       ; NMOS, S=GND, G=IN_N,  D=OUT_P
    V0  VDD 0     1.8

The token order in each MOSFET line is interpreted as
``NAME SOURCE GATE DRAIN`` — the physically natural layout of a
cross-coupled latch (PMOS sources at VDD, NMOS sources at GND).

Why this is bistable
--------------------
The two PMOS devices form a cross-coupled pair with positive feedback:
whichever output is higher keeps its partner off, reinforcing the
asymmetry. With a symmetric bias ``IN_P = IN_N`` the circuit has two
stable equilibria -- roughly (OUT_P HIGH, OUT_N LOW) and
(OUT_P LOW, OUT_N HIGH) -- plus one unstable metastable point where
``OUT_P = OUT_N``.

What the demo does
------------------
1. Builds the circuit in ``sycan`` with numeric MOSFET parameters.
2. Pulls the symbolic nonlinear KCL residuals out via
   ``sycan.build_residuals`` and lambdifies them (plus their Jacobian)
   to ``numpy`` callables.
3. Runs Newton iteration from a random initial guess per trial.
4. Collects the converged ``V(OUT_P)`` from a few hundred Monte-Carlo
   runs and plots a histogram (matplotlib). The distribution is
   clearly bimodal — the bistability is visible at a glance.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import sympy as sp

from sycan import Circuit, build_residuals


# ---------------------------------------------------------------------------
# 1. Build the circuit
# ---------------------------------------------------------------------------

# --- 180 nm CMOS process parameters ----------------------------------------
# Gate-oxide capacitance Cox = eps_ox / t_ox, with eps_ox = 3.9*eps_0 and
# t_ox ~ 4 nm for a 1.8 V core device => Cox ~ 8.6 mF/m**2 = 8.6 fF/um**2.
EPS_OX = 3.9 * 8.854e-12          # F/m
T_OX = 4e-9                        # m
Cox_val = EPS_OX / T_OX            # ~ 8.63e-3 F/m**2

# Surface-channel mobilities at 300 K (typical 180 nm textbook values).
mu_n_val = 270e-4                  # 270 cm**2/(V*s) in SI
mu_p_val = 80e-4                   # 80 cm**2/(V*s)  in SI

L_val   = 180e-9                   # minimum channel length
W_n_val = 5.0e-6                   # NMOS drivers
W_p_val = 1.0e-6                   # PMOS pullups
V_TH_mean = 0.5                    # |V_TH| magnitude, same for N and P
V_TH_sigma = 0.05                  # ~50 mV threshold mismatch (3 sigma ~ 150 mV)
lam_val = 0.1                      # channel-length modulation

VDD_val = 1.8
V_IN_val = VDD_val / 2             # symmetric -> bistable regime

circuit = Circuit()
circuit.add_vsource("V0",   "VDD",  "0", VDD_val)
circuit.add_vsource("VINP", "IN_P", "0", V_IN_val)
circuit.add_vsource("VINN", "IN_N", "0", V_IN_val)

# Keep V_TH per-device symbolic so each Monte-Carlo trial can re-sample
# the four thresholds without rebuilding the residual system.
vth_syms = {n: sp.Symbol(f"V_TH_{n}") for n in ("MP0", "MP1", "MN0", "MN1")}

_pmos_kw = dict(mu_n=mu_p_val, Cox=Cox_val, W=W_p_val, L=L_val, lam=lam_val)
_nmos_kw = dict(mu_n=mu_n_val, Cox=Cox_val, W=W_n_val, L=L_val, lam=lam_val)

# MP0 VDD OUT_P OUT_N -> S=VDD, G=OUT_P, D=OUT_N (PMOS pulling OUT_N up).
circuit.add_pmos_l1("MP0", drain="OUT_N", gate="OUT_P", source="VDD",
                    V_TH=vth_syms["MP0"], **_pmos_kw)
circuit.add_pmos_l1("MP1", drain="OUT_P", gate="OUT_N", source="VDD",
                    V_TH=vth_syms["MP1"], **_pmos_kw)
# MN0 0 IN_P OUT_N -> S=GND, G=IN_P, D=OUT_N (NMOS pulling OUT_N down).
circuit.add_nmos_l1("MN0", drain="OUT_N", gate="IN_P", source="0",
                    V_TH=vth_syms["MN0"], **_nmos_kw)
circuit.add_nmos_l1("MN1", drain="OUT_P", gate="IN_N", source="0",
                    V_TH=vth_syms["MN1"], **_nmos_kw)


# ---------------------------------------------------------------------------
# 2. Symbolic residuals -> numerical f(x) and J(x)
# ---------------------------------------------------------------------------

unknowns, residuals = build_residuals(circuit, mode="dc")
unknowns = list(unknowns)
print(f"Unknowns ({len(unknowns)}): {[str(u) for u in unknowns]}")

# Four threshold parameters appended after the unknowns.
vth_params = [vth_syms["MP0"], vth_syms["MP1"], vth_syms["MN0"], vth_syms["MN1"]]
all_args = unknowns + vth_params

f_np = sp.lambdify(all_args, residuals, modules="numpy")
J_np = sp.lambdify(
    all_args,
    sp.Matrix(residuals).jacobian(unknowns),
    modules="numpy",
)


# ---------------------------------------------------------------------------
# 3. Newton's method
# ---------------------------------------------------------------------------

def newton(x0: np.ndarray, vth: np.ndarray,
           tol: float = 1e-9, max_iter: int = 60):
    x = np.asarray(x0, dtype=float).flatten().copy()
    vth = list(vth)
    for _ in range(max_iter):
        f = np.asarray(f_np(*x, *vth), dtype=float).flatten()
        if np.linalg.norm(f) < tol:
            return x, True
        J = np.asarray(J_np(*x, *vth), dtype=float)
        try:
            dx = np.linalg.solve(J, -f)
        except np.linalg.LinAlgError:
            return x, False
        # Simple step damping keeps iterates inside a reasonable voltage
        # range when the saturation-only MOSFET model diverges wildly.
        step_norm = np.linalg.norm(dx)
        if step_norm > 1.0:
            dx *= 1.0 / step_norm
        x += dx
    return x, False


# ---------------------------------------------------------------------------
# 4. Monte Carlo over random initial conditions
# ---------------------------------------------------------------------------

rng = np.random.default_rng(42)
N_TRIALS = 10000

out_p_idx = unknowns.index(sp.Symbol("V(OUT_P)"))
out_n_idx = unknowns.index(sp.Symbol("V(OUT_N)"))

outs_p: list[float] = []
outs_n: list[float] = []
for _ in range(N_TRIALS):
    # Two sources of randomness per trial:
    #   1. per-device V_TH mismatch ~ N(V_TH_mean, V_TH_sigma)
    #   2. initial node voltages ~ N(VDD/2, VDD/4)
    vth_sample = rng.normal(loc=V_TH_mean, scale=V_TH_sigma, size=4)
    x0 = rng.normal(loc=VDD_val / 2, scale=VDD_val / 4, size=len(unknowns))
    x_sol, converged = newton(x0, vth_sample)
    if converged:
        outs_p.append(x_sol[out_p_idx])
        outs_n.append(x_sol[out_n_idx])

outs_p = np.array(outs_p)
outs_n = np.array(outs_n)


# ---------------------------------------------------------------------------
# 5. Summarize
# ---------------------------------------------------------------------------

print(f"\n{len(outs_p)} / {N_TRIALS} Newton runs converged")
print(f"V(OUT_P) range : [{outs_p.min():+6.3f}, {outs_p.max():+6.3f}] V")
print(f"V(OUT_N) range : [{outs_n.min():+6.3f}, {outs_n.max():+6.3f}] V")

# Bucket against the physically meaningful midpoint (VDD/2) rather than
# the sample midrange -- the Gaussian tails occasionally pull Newton to
# unphysical fixed points that would otherwise distort the threshold.
mid = VDD_val / 2
n_high = int((outs_p > mid).sum())
n_low = int((outs_p < mid).sum())
print(
    f"\nBistable buckets (mid = {mid:+6.3f} V):  "
    f"OUT_P 'HIGH' {n_high}   OUT_P 'LOW' {n_low}"
)

fig, (ax, ax_joint) = plt.subplots(1, 2, figsize=(12, 4.2))

# Shared histogram range covering both outputs.
all_vals = np.concatenate([outs_p, outs_n])
pad = 0.1 * (all_vals.max() - all_vals.min())
hist_range = (all_vals.min() - pad, all_vals.max() + pad)

ax.hist(
    outs_p, bins=40, range=hist_range,
    alpha=0.65, color="#4C72B0", edgecolor="black", label="V(OUT_P)",
)
ax.hist(
    outs_n, bins=40, range=hist_range,
    alpha=0.65, color="#DD8452", edgecolor="black", label="V(OUT_N)",
)
ax.axvline(0.0, linestyle=":", color="gray")
ax.axvline(VDD_val, linestyle=":", color="gray")
ax.axvline(mid, linestyle="--", color="red", label="VDD/2")
ax.set_xlabel("voltage  [V]")
ax.set_ylabel("count")
ax.set_title(
    f"Monte-Carlo histogram  ({len(outs_p)} Newton runs, "
    f"sigma(V_TH) = {V_TH_sigma*1000:.0f} mV, "
    f"IN_P = IN_N = {V_IN_val:.2f} V)"
)
ax.legend(loc="upper center")

# Joint scatter shows the two bistable attractors on the (OUT_P, OUT_N) plane.
ax_joint.scatter(outs_p, outs_n, s=6, alpha=0.35, color="#4C72B0")
ax_joint.axvline(mid, linestyle="--", color="red", alpha=0.6)
ax_joint.axhline(mid, linestyle="--", color="red", alpha=0.6)
ax_joint.plot([hist_range[0], hist_range[1]], [hist_range[0], hist_range[1]],
              linestyle=":", color="gray", linewidth=1, label="OUT_P = OUT_N")
ax_joint.set_xlim(hist_range)
ax_joint.set_ylim(hist_range)
ax_joint.set_xlabel("V(OUT_P)  [V]")
ax_joint.set_ylabel("V(OUT_N)  [V]")
ax_joint.set_title("Joint distribution of (OUT_P, OUT_N)")
ax_joint.legend(loc="upper left")
ax_joint.set_aspect("equal", adjustable="box")

fig.tight_layout()

out_path = Path(__file__).with_suffix(".png")
fig.savefig(out_path, dpi=150)
print(f"\nHistogram saved to {out_path}")
plt.show()
