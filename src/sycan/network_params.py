"""Network-parameter conversions for two-port (and n-port where it
applies) representations: Z, Y, S, ABCD, T.

Conventions follow Pozar, *Microwave Engineering*:

* ``Z`` (impedance):       ``V = Z @ I``
* ``Y`` (admittance):      ``I = Y @ V``
* ``S`` (scattering):      ``b = S @ a`` with reference impedance ``Z0``
* ``ABCD`` (chain):        ``[V1; I1] = ABCD @ [V2; -I2]``  (2-port only)
* ``T`` (transfer):        ``[a1; b1] = T @ [b2; a2]``      (2-port only)

All matrices are ``sympy.Matrix`` so the same code works for symbolic
parameters and for numeric evaluation via ``.subs(...)`` / ``.evalf()``.
``Z0`` defaults to 50 Ω; pass a sympy ``Symbol`` for symbolic work or a
sympy ``Matrix`` (diagonal) for n-port with per-port reference
impedances.
"""
from __future__ import annotations

from typing import Union

import sympy as sp

MatrixLike = Union[sp.Matrix, list, tuple]
Z0Like = Union[sp.Expr, int, float, sp.Matrix]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _M(m: MatrixLike) -> sp.Matrix:
    return m if isinstance(m, sp.Matrix) else sp.Matrix(m)


def _check_2x2(m: sp.Matrix, name: str) -> None:
    if m.shape != (2, 2):
        raise ValueError(f"{name} must be 2x2 for this conversion, got {m.shape}")


def _Z0_diag(Z0: Z0Like, n: int) -> sp.Matrix:
    """Reference-impedance matrix as an n×n diagonal."""
    if isinstance(Z0, sp.Matrix):
        if Z0.shape == (n, n):
            return Z0
        if Z0.shape in ((n, 1), (1, n)):
            return sp.diag(*list(Z0))
        raise ValueError(f"Z0 matrix shape {Z0.shape} incompatible with n={n}")
    return sp.eye(n) * sp.sympify(Z0)


# ---------------------------------------------------------------------------
# Z <-> Y
# ---------------------------------------------------------------------------

def z_to_y(Z: MatrixLike) -> sp.Matrix:
    """Y = Z⁻¹ (n-port)."""
    return _M(Z).inv()


def y_to_z(Y: MatrixLike) -> sp.Matrix:
    """Z = Y⁻¹ (n-port)."""
    return _M(Y).inv()


# ---------------------------------------------------------------------------
# Z/Y <-> S
# ---------------------------------------------------------------------------

def z_to_s(Z: MatrixLike, Z0: Z0Like = 50) -> sp.Matrix:
    """S = (Z − Z0)·(Z + Z0)⁻¹ (n-port, real Z0)."""
    Z = _M(Z)
    Z0m = _Z0_diag(Z0, Z.shape[0])
    return (Z - Z0m) * (Z + Z0m).inv()


def s_to_z(S: MatrixLike, Z0: Z0Like = 50) -> sp.Matrix:
    """Z = (I − S)⁻¹·(I + S)·Z0 (n-port, real Z0)."""
    S = _M(S)
    n = S.shape[0]
    I = sp.eye(n)
    Z0m = _Z0_diag(Z0, n)
    return (I - S).inv() * (I + S) * Z0m


def y_to_s(Y: MatrixLike, Z0: Z0Like = 50) -> sp.Matrix:
    """S = (I − Z0·Y)·(I + Z0·Y)⁻¹."""
    Y = _M(Y)
    n = Y.shape[0]
    I = sp.eye(n)
    Z0m = _Z0_diag(Z0, n)
    return (I - Z0m * Y) * (I + Z0m * Y).inv()


def s_to_y(S: MatrixLike, Z0: Z0Like = 50) -> sp.Matrix:
    """Y = Y0·(I − S)·(I + S)⁻¹  with Y0 = 1/Z0."""
    S = _M(S)
    n = S.shape[0]
    I = sp.eye(n)
    Z0m = _Z0_diag(Z0, n)
    return Z0m.inv() * (I - S) * (I + S).inv()


# ---------------------------------------------------------------------------
# Z/Y <-> ABCD (2-port only)
# ---------------------------------------------------------------------------

def z_to_abcd(Z: MatrixLike) -> sp.Matrix:
    Z = _M(Z); _check_2x2(Z, "Z")
    Z11, Z12 = Z[0, 0], Z[0, 1]
    Z21, Z22 = Z[1, 0], Z[1, 1]
    return sp.Matrix([
        [Z11 / Z21, (Z11 * Z22 - Z12 * Z21) / Z21],
        [1 / Z21,                     Z22 / Z21],
    ])


def abcd_to_z(M: MatrixLike) -> sp.Matrix:
    M = _M(M); _check_2x2(M, "ABCD")
    A, B, C, D = M[0, 0], M[0, 1], M[1, 0], M[1, 1]
    return sp.Matrix([
        [A / C, (A * D - B * C) / C],
        [1 / C,                D / C],
    ])


def y_to_abcd(Y: MatrixLike) -> sp.Matrix:
    Y = _M(Y); _check_2x2(Y, "Y")
    Y11, Y12 = Y[0, 0], Y[0, 1]
    Y21, Y22 = Y[1, 0], Y[1, 1]
    return sp.Matrix([
        [-Y22 / Y21,                       -1 / Y21],
        [-(Y11 * Y22 - Y12 * Y21) / Y21,  -Y11 / Y21],
    ])


def abcd_to_y(M: MatrixLike) -> sp.Matrix:
    M = _M(M); _check_2x2(M, "ABCD")
    A, B, C, D = M[0, 0], M[0, 1], M[1, 0], M[1, 1]
    return sp.Matrix([
        [D / B, -(A * D - B * C) / B],
        [-1 / B,                A / B],
    ])


# ---------------------------------------------------------------------------
# ABCD <-> S (2-port only)
# ---------------------------------------------------------------------------

def abcd_to_s(M: MatrixLike, Z0: Z0Like = 50) -> sp.Matrix:
    """ABCD → S, single real reference impedance Z0 on both ports."""
    M = _M(M); _check_2x2(M, "ABCD")
    A, B, C, D = M[0, 0], M[0, 1], M[1, 0], M[1, 1]
    Z0 = sp.sympify(Z0)
    den = A + B / Z0 + C * Z0 + D
    return sp.Matrix([
        [(A + B / Z0 - C * Z0 - D) / den,  2 * (A * D - B * C) / den],
        [2 / den,                          (-A + B / Z0 - C * Z0 + D) / den],
    ])


def s_to_abcd(S: MatrixLike, Z0: Z0Like = 50) -> sp.Matrix:
    """S → ABCD, single real reference impedance Z0 on both ports."""
    S = _M(S); _check_2x2(S, "S")
    S11, S12 = S[0, 0], S[0, 1]
    S21, S22 = S[1, 0], S[1, 1]
    Z0 = sp.sympify(Z0)
    den = 2 * S21
    return sp.Matrix([
        [((1 + S11) * (1 - S22) + S12 * S21) / den,
         Z0 * ((1 + S11) * (1 + S22) - S12 * S21) / den],
        [(1 / Z0) * ((1 - S11) * (1 - S22) - S12 * S21) / den,
         ((1 - S11) * (1 + S22) + S12 * S21) / den],
    ])


# ---------------------------------------------------------------------------
# S <-> T (2-port only; T is the cascading "transfer scattering" matrix)
# ---------------------------------------------------------------------------

def s_to_t(S: MatrixLike) -> sp.Matrix:
    """S → T, with [a1; b1] = T·[b2; a2] (Pozar convention)."""
    S = _M(S); _check_2x2(S, "S")
    S11, S12 = S[0, 0], S[0, 1]
    S21, S22 = S[1, 0], S[1, 1]
    return sp.Matrix([
        [(S12 * S21 - S11 * S22) / S21,  S11 / S21],
        [-S22 / S21,                      1   / S21],
    ])


def t_to_s(T: MatrixLike) -> sp.Matrix:
    T = _M(T); _check_2x2(T, "T")
    T11, T12 = T[0, 0], T[0, 1]
    T21, T22 = T[1, 0], T[1, 1]
    return sp.Matrix([
        [T12 / T22,  (T11 * T22 - T12 * T21) / T22],
        [1   / T22, -T21 / T22],
    ])
