"""Continuous-time analog filter prototype polynomials.

Each constructor returns ``(numerator, denominator)`` as sympy
expressions in a Laplace variable ``s``, with the standard normalised
DC gain (|H(0)| = 1, except Chebyshev I of even order where the
textbook value 1/sqrt(1+eps^2) is used).

Pass your own ``s`` symbol if you want the polynomials to share it
with the rest of an expression; otherwise a fresh ``sp.Symbol("s")``
is created.
"""
from __future__ import annotations

import math

import sympy as sp


def _default_s(s):
    return sp.Symbol("s") if s is None else s


def _poly_from_poles(poles, s) -> sp.Expr:
    """Build a real-coefficient sympy polynomial from a list of poles.

    Imaginary parts cancel by complex-conjugate symmetry; we discard
    the round-off residue rather than carrying it through.
    """
    expr = sp.S.One
    for p in poles:
        expr *= (s - sp.sympify(complex(p)))
    coeffs = sp.Poly(sp.expand(expr), s).all_coeffs()  # high-to-low degree
    coeffs = [complex(c).real for c in coeffs]
    n = len(coeffs) - 1
    return sum(sp.Float(c, 6) * s**(n - i) for i, c in enumerate(coeffs))


# ---------------------------------------------------------------------------
# Butterworth: maximally-flat magnitude. Cutoff at 1 rad/s.
# ---------------------------------------------------------------------------

def butterworth(n: int, s: sp.Symbol | None = None) -> tuple[sp.Expr, sp.Expr]:
    s = _default_s(s)
    poles = [
        complex(math.cos(math.pi * (2 * k + n - 1) / (2 * n)),
                math.sin(math.pi * (2 * k + n - 1) / (2 * n)))
        for k in range(1, n + 1)
    ]
    denom = _poly_from_poles(poles, s)
    return sp.Integer(1), denom


# ---------------------------------------------------------------------------
# Chebyshev type I: equiripple in the passband. Passband edge at 1 rad/s.
# ---------------------------------------------------------------------------

def chebyshev1(
    n: int,
    ripple_db: float,
    s: sp.Symbol | None = None,
) -> tuple[sp.Expr, sp.Expr]:
    s = _default_s(s)
    eps = math.sqrt(10 ** (ripple_db / 10) - 1)
    mu = math.asinh(1 / eps) / n
    poles = []
    for k in range(1, n + 1):
        theta = math.pi * (2 * k - 1) / (2 * n)
        poles.append(complex(-math.sinh(mu) * math.sin(theta),
                             math.cosh(mu) * math.cos(theta)))
    denom = _poly_from_poles(poles, s)
    H0 = 1.0 if n % 2 else 1.0 / math.sqrt(1 + eps * eps)
    num = sp.Float(H0 * float(denom.subs(s, 0)), 6)
    return num, denom


# ---------------------------------------------------------------------------
# Bessel (Thomson): maximally-flat group delay. Unit-delay normalization,
# i.e. tau(0) = 1, NOT a -3 dB cutoff at 1 rad/s.
# ---------------------------------------------------------------------------

def bessel(n: int, s: sp.Symbol | None = None) -> tuple[sp.Expr, sp.Expr]:
    s = _default_s(s)
    a = [sp.factorial(2*n - k) / (sp.Integer(2)**(n - k)
                                  * sp.factorial(k) * sp.factorial(n - k))
         for k in range(n + 1)]
    denom = sum(a[k] * s**k for k in range(n + 1))
    return a[0], denom
