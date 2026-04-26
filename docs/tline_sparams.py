"""S-parameters of a lossless transmission line — symbolic + plot.

Uses the closed-form ABCD matrix of a lossless line

    ABCD = [[cosh(s*td),       Zc * sinh(s*td)],
            [sinh(s*td) / Zc,  cosh(s*td)     ]]

converts it to S via ``sycan.abcd_to_s`` (reference impedance ``Z0``),
then sweeps electrical length ``theta = omega*td`` from 0 to 2*pi and
plots ``|S11|``, ``|S21|`` and ``arg(S21)``.

A matched line (Zc == Z0) gives the trivial result ``|S11|=0``,
``|S21|=1``. The interesting case below uses a 75 Ω line referenced
to a 50 Ω system, which exhibits the textbook periodic mismatch
ripple in ``|S11|`` and the linear phase rolloff in ``S21``.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import sympy as sp

from sycan import abcd_to_s


# ---------------------------------------------------------------------------
# 1. Symbolic ABCD -> S
# ---------------------------------------------------------------------------

s, td, Zc, Z0 = sp.symbols("s td Zc Z0", positive=True)
theta = s * td

ABCD = sp.Matrix([
    [sp.cosh(theta),        Zc * sp.sinh(theta)],
    [sp.sinh(theta) / Zc,   sp.cosh(theta)],
])

S_sym = abcd_to_s(ABCD, Z0)
S_sym = sp.simplify(S_sym)
print("Symbolic S-matrix of a lossless line (s, td, Zc, Z0):")
sp.pprint(S_sym)


# ---------------------------------------------------------------------------
# 2. Numerical sweep: 75 Ω line in a 50 Ω system
# ---------------------------------------------------------------------------

Zc_val = 75.0
Z0_val = 50.0

# Lambdify against (s, Zc, Z0). theta = omega*td, so for a unit td we
# can sweep s = j*theta directly.
S_eval = sp.lambdify((s, td, Zc, Z0), S_sym, modules="numpy")

theta_axis = np.linspace(1e-6, 2 * np.pi, 401)
td_val = 1.0          # arbitrary; only s*td matters
s_axis = 1j * theta_axis / td_val

S = np.stack([np.asarray(S_eval(sv, td_val, Zc_val, Z0_val), dtype=complex)
              for sv in s_axis])
S11 = S[:, 0, 0]
S21 = S[:, 1, 0]


# ---------------------------------------------------------------------------
# 3. Plot
# ---------------------------------------------------------------------------

fig, (ax_mag, ax_phase) = plt.subplots(2, 1, figsize=(7, 6), sharex=True)

ax_mag.plot(theta_axis / np.pi, 20 * np.log10(np.abs(S11)), label="|S11|")
ax_mag.plot(theta_axis / np.pi, 20 * np.log10(np.abs(S21)), label="|S21|")
ax_mag.set_ylabel("magnitude [dB]")
ax_mag.set_title(f"Lossless TLINE  (Zc={Zc_val:.0f} Ω in a Z0={Z0_val:.0f} Ω system)")
ax_mag.grid(True, alpha=0.3)
ax_mag.legend(loc="best")

ax_phase.plot(theta_axis / np.pi, np.unwrap(np.angle(S11)), label="∠S11")
ax_phase.plot(theta_axis / np.pi, np.unwrap(np.angle(S21)), label="∠S21")
ax_phase.set_xlabel(r"electrical length $\omega \tau / \pi$")
ax_phase.set_ylabel("phase [rad]")
ax_phase.grid(True, alpha=0.3)
ax_phase.legend(loc="best")

fig.tight_layout()

out = Path(__file__).with_suffix(".png")
fig.savefig(out, dpi=130)
print(f"\nSaved {out}")


# ---------------------------------------------------------------------------
# 4. Sanity: matched line is reflectionless and lossless
# ---------------------------------------------------------------------------

S_matched = sp.simplify(S_sym.subs(Zc, Z0))
print("\nMatched line (Zc = Z0):")
sp.pprint(S_matched)
