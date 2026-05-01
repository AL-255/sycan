"""Analog low-pass filter prototype: Butterworth / Chebyshev I / Bessel.

Reads the family / order / ripple from the page controls (the panel
above the editor), builds the normalised H(s) symbolically via the
prototypes shipped in ``sycan.polynomials``, prints the LaTeX, and
returns an inline SVG Bode plot via ``sycan.bode_svg``.
"""
import numpy as np
from sycan import cas as cas

from sycan import bessel, bode_svg, butterworth, chebyshev1

# Read user choice from the page (defaults make the script also work
# when copy-pasted into a plain Python REPL).
family, order, ripple_db = "butterworth", 4, 1.0
try:
    import js
    family = js.document.getElementById("filter-family").value
    order = int(js.document.getElementById("filter-order").value)
    ripple_db = float(js.document.getElementById("filter-ripple").value)
except Exception:
    pass

s = cas.Symbol("s")

if family == "butterworth":
    num, denom = butterworth(order, s)
    title = f"Butterworth, n={order}  (ω_c = 1 rad/s)"
elif family == "chebyshev":
    num, denom = chebyshev1(order, ripple_db, s)
    title = f"Chebyshev I, n={order}, ripple={ripple_db:g} dB  (ω_p = 1 rad/s)"
elif family == "bessel":
    num, denom = bessel(order, s)
    title = f"Bessel, n={order}  (unit-delay normalization)"
else:
    raise ValueError(f"unknown family: {family!r}")

H = num / denom
print(f"$$H(s) = {cas.latex(H)}$$")
print()
print(title)

# Numerical Bode response.
omegas = np.logspace(-2, 2, 401)
H_fn = cas.lambdify(s, H, modules="numpy")
H_vals = np.asarray(H_fn(1j * omegas), dtype=complex)
mag_db = 20.0 * np.log10(np.maximum(np.abs(H_vals), 1e-12))
phase_deg = np.degrees(np.unwrap(np.angle(H_vals)))

bode_svg(omegas, mag_db, phase_deg, title)
