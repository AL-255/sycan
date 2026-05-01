"""sycan.cas backend that forwards to :mod:`symengine`.

SymEngine is a fast C++ symbolic core with Python bindings, but its
public surface is narrower than sympy's: it does not ship ``simplify``,
``solve``, ``factor``, ``Poly``, ``lambdify(modules="numpy")``, etc.
This module exposes the same names sycan reaches for through
:mod:`sycan.cas` by:

1. Forwarding names that exist natively in symengine via the standard
   ``__getattr__`` proxy (so ``Symbol``, ``Matrix``, ``exp``, ``diff``…
   come straight from the C++ core).
2. Providing bridge wrappers for names symengine lacks. The bridges
   sympify the inputs, run the corresponding sympy operation, and
   ``se.sympify`` the result back into symengine objects so the rest
   of sycan keeps working with one expression type.

Conversions in either direction are exact for the algebraic
expressions sycan builds — the only places this matters in practice
are the heavy symbolic passes (``solve``, ``simplify``), which dominate
the cost anyway, so the round-trip is not a bottleneck.
"""
from __future__ import annotations

import warnings
from typing import Any

from fractions import Fraction as _Fraction

import symengine as _se
import sympy as _sp


def _emit_fallback(fn_name: str, reason: str) -> None:
    """Warn that a native symengine call failed and we're going to sympy.

    Uses :class:`RuntimeWarning` so callers can suppress / capture via
    the standard warnings filter (``warnings.filterwarnings``). The text
    matches a stable format so downstream tooling can grep for it.
    """
    warnings.warn(
        f"SymEngine Function {fn_name} Failed {reason}. Falling back to sympy",
        RuntimeWarning,
        stacklevel=3,
    )

# ``solve`` and the set classes live in the Cython wrapper, not at the
# top level of the ``symengine`` package. We pull them in by hand so the
# bridge can dispatch single-equation polynomial solves to the native
# C++ solver.
from symengine.lib.symengine_wrapper import (  # type: ignore[import-not-found]
    EmptySet as _EmptySet,
    FiniteSet as _FiniteSet,
    UniversalSet as _UniversalSet,
    solve as _se_solve,
)


# ---------------------------------------------------------------------------
# Conversion helpers between symengine and sympy.
# ---------------------------------------------------------------------------
def _to_sp(obj: Any) -> Any:
    """Convert a symengine object (or container thereof) to sympy."""
    if isinstance(obj, list):
        return [_to_sp(x) for x in obj]
    if isinstance(obj, tuple):
        return tuple(_to_sp(x) for x in obj)
    if isinstance(obj, dict):
        return {_to_sp(k): _to_sp(v) for k, v in obj.items()}
    if isinstance(obj, set):
        return {_to_sp(x) for x in obj}
    return _sp.sympify(obj)


def _to_se(obj: Any) -> Any:
    """Convert a sympy object (or container thereof) to symengine."""
    if isinstance(obj, list):
        return [_to_se(x) for x in obj]
    if isinstance(obj, tuple):
        return tuple(_to_se(x) for x in obj)
    if isinstance(obj, dict):
        return {_to_se(k): _to_se(v) for k, v in obj.items()}
    if isinstance(obj, set):
        return {_to_se(x) for x in obj}
    try:
        return _se.sympify(obj)
    except Exception:
        # Booleans, exception classes and Python primitives fall through
        # unchanged — symengine can't sympify them but they are fine as-is.
        return obj


# ---------------------------------------------------------------------------
# Sympy bridges for names symengine doesn't ship.
#
# Each bridge sympifies its arguments, calls sympy, and converts the
# result back to symengine so callers see a uniform expression type.
# ---------------------------------------------------------------------------
def _to_basic(expr):
    """Coerce input to a symengine ``Basic`` so the native instance
    methods (``simplify``, ``expand``, ``as_numer_denom``) are reachable.
    Falls back to :func:`se.sympify` for anything that isn't already
    a Basic — e.g. plain ints, sympy expressions handed in by callers.
    """
    if isinstance(expr, _se.Basic):
        return expr
    return _se.sympify(expr)


def simplify(expr, *args, **kwargs):
    # ``Basic.simplify()`` is the C++ canonicaliser. It handles rational
    # cancellation, basic trig / hyperbolic identities, and sum
    # combination — covers every shape sycan asks ``simplify`` for.
    try:
        return _to_basic(expr).simplify()
    except Exception as exc:
        _emit_fallback("simplify", f"{type(exc).__name__}: {exc}")
        return _to_se(_sp.simplify(_to_sp(expr), *args, **kwargs))


def cancel(expr, *args, **kwargs):
    # No standalone ``cancel``; ``simplify`` performs the rational
    # cancellation we need.
    try:
        return _to_basic(expr).simplify()
    except Exception as exc:
        _emit_fallback("cancel", f"{type(exc).__name__}: {exc}")
        return _to_se(_sp.cancel(_to_sp(expr), *args, **kwargs))


def together(expr, *args, **kwargs):
    # ``together(a/x + b/y) = (a*y + b*x)/(x*y)`` is exactly what
    # ``as_numer_denom`` returns (joined back as a ratio).
    try:
        n, d = _to_basic(expr).as_numer_denom()
        return n / d
    except Exception as exc:
        _emit_fallback("together", f"{type(exc).__name__}: {exc}")
        return _to_se(_sp.together(_to_sp(expr), *args, **kwargs))


def fraction(expr, *args, **kwargs):
    try:
        return _to_basic(expr).as_numer_denom()
    except Exception as exc:
        _emit_fallback("fraction", f"{type(exc).__name__}: {exc}")
        return tuple(_to_se(p) for p in _sp.fraction(_to_sp(expr), *args, **kwargs))


def trigsimp(expr, *args, **kwargs):
    # ``Basic.simplify`` already collapses sin² + cos² and similar
    # identities; sympy's ``trigsimp`` does more (e.g. product-to-sum)
    # but sycan only ever uses the identity form.
    try:
        return _to_basic(expr).simplify()
    except Exception as exc:
        _emit_fallback("trigsimp", f"{type(exc).__name__}: {exc}")
        return _to_se(_sp.trigsimp(_to_sp(expr), *args, **kwargs))


def factor(expr, *args, **kwargs):
    # Polynomial factorisation isn't exposed in the symengine wrapper
    # (the wrapper's ``factor`` is integer-only). Bridge to sympy.
    return _to_se(_sp.factor(_to_sp(expr), *args, **kwargs))


def expand_log(expr, *args, **kwargs):
    return _to_se(_sp.expand_log(_to_sp(expr), *args, **kwargs))


def integrate(*args, **kwargs):
    return _to_se(_sp.integrate(*[_to_sp(a) for a in args], **kwargs))


def limit(*args, **kwargs):
    return _to_se(_sp.limit(*[_to_sp(a) for a in args], **kwargs))


def factorial(n, *args, **kwargs):
    return _to_se(_sp.factorial(_to_sp(n), *args, **kwargs))


class _SENativeUnsupported(Exception):
    """Internal sentinel — symengine cannot handle this solve shape.

    Carries the ``reason`` string so :func:`solve` can include it in the
    fallback warning instead of swallowing it silently.
    """


def _try_se_solve(eq, var):
    """Native symengine solve for a single polynomial equation.

    Returns a list of solutions on success, or raises
    :class:`_SENativeUnsupported` with a human-readable reason. The C++
    solver only handles single polynomial equations in one variable, but
    where it applies it's an order of magnitude faster than sympifying
    and routing through ``sympy.solve``.
    """
    if not isinstance(var, _se.Symbol):
        raise _SENativeUnsupported(
            f"second arg is not a Symbol (got {type(var).__name__})"
        )
    expr = eq
    if isinstance(expr, _se.Equality):
        # ``Eq(a, b).args`` is ``(a, b)``; the polynomial form solve()
        # wants is ``a - b``.
        a, b = expr.args
        expr = a - b
    try:
        expr = _se.sympify(expr)
    except Exception as exc:
        raise _SENativeUnsupported(f"sympify of equation failed: {exc}") from exc
    try:
        result = _se_solve(expr, var)
    except (RuntimeError, ValueError, TypeError) as exc:
        raise _SENativeUnsupported(f"{type(exc).__name__}: {exc}") from exc
    if isinstance(result, _FiniteSet):
        return list(result.args)
    if isinstance(result, _EmptySet):
        return []
    if isinstance(result, _UniversalSet):
        # Solution is every value; sympy.solve returns ``[]`` for the
        # rare ``0 == 0`` case which downstream callers special-case as
        # "no useful root", so we fall back to that representation.
        return []
    raise _SENativeUnsupported(
        f"unexpected result type {type(result).__name__}"
    )


def solve(*args, **kwargs):
    """Symbolic solver bridge.

    Sycan calls this in two shapes:
    * ``solve(eqs, syms, dict=True)`` — list of dicts (system).
    * ``solve(eq, var)``              — list of expressions.

    The single-equation shape with a polynomial equation goes to the
    native C++ symengine solver. Everything else (systems, ``dict=True``,
    transcendental equations) bridges to sympy, with the result
    converted back to symengine so downstream ``isinstance`` checks and
    substitutions keep working.

    Native attempts that can't close the equation emit a single
    ``RuntimeWarning`` and retry through sympy; the call still returns
    a valid result.
    """
    if len(args) == 2 and not kwargs:
        try:
            return _try_se_solve(args[0], args[1])
        except _SENativeUnsupported as exc:
            _emit_fallback("solve", str(exc))
        # fall through to sympy bridge below.
    sp_args = [_to_sp(a) for a in args]
    sols = _sp.solve(*sp_args, **kwargs)
    return _to_se(sols)


def nsolve(*args, **kwargs):
    return _to_se(_sp.nsolve(*[_to_sp(a) for a in args], **kwargs))


def pprint(expr, *args, **kwargs):
    return _sp.pprint(_to_sp(expr), *args, **kwargs)


# Lambdify with ``modules="numpy"`` is what sycan's damped-Newton fallback
# wants. Symengine's lambdify uses a different signature (``backend=``),
# and the numpy code path differs subtly in how Matrix outputs are
# returned, so we delegate to sympy for any call that asks for a module
# selection.
def lambdify(args, expr, *extra_exprs, modules=None, **kwargs):
    if modules is None and not extra_exprs:
        try:
            return _se.lambdify(args, expr, **kwargs)
        except TypeError:
            modules = "numpy"  # fall through to sympy
    sp_args = _to_sp(args)
    if extra_exprs:
        sp_exprs = [_to_sp(e) for e in (expr, *extra_exprs)]
        return _sp.lambdify(sp_args, sp_exprs, modules=modules, **kwargs)
    return _sp.lambdify(sp_args, _to_sp(expr), modules=modules, **kwargs)


# ---------------------------------------------------------------------------
# Polynomial helpers — symengine has no Poly class.
# ---------------------------------------------------------------------------
PolynomialError = _sp.PolynomialError


class _PolyBridge:
    """Thin proxy around :class:`sympy.Poly` that returns symengine
    coefficients, so the rest of the codebase keeps a uniform expression
    type."""

    def __init__(self, expr, *gens, **kwargs):
        self._sp_poly = _sp.Poly(
            _to_sp(expr),
            *[_to_sp(g) for g in gens],
            **kwargs,
        )

    def all_coeffs(self):
        return [_to_se(c) for c in self._sp_poly.all_coeffs()]

    def __getattr__(self, name):
        return getattr(self._sp_poly, name)


Poly = _PolyBridge


# ---------------------------------------------------------------------------
# Rational accepts decimal strings in sympy ("0.5" → 1/2) but symengine
# only accepts (numerator, denominator). Wrap so call sites that pass a
# numeric string keep working — the SPICE parser, in particular, hands
# us mantissa strings straight from the netlist.
# ---------------------------------------------------------------------------
def Rational(*args, **kwargs):
    if len(args) == 1 and isinstance(args[0], str):
        frac = _Fraction(args[0])
        return _se.Rational(frac.numerator, frac.denominator)
    return _se.Rational(*args, **kwargs)


# ---------------------------------------------------------------------------
# Default delegation for everything symengine ships natively (Symbol,
# Matrix, sqrt, exp, diff, eye, zeros, S, oo, pi, …).
# ---------------------------------------------------------------------------
def __getattr__(name: str) -> Any:
    if name.startswith("_"):
        raise AttributeError(name)
    return getattr(_se, name)


def __dir__() -> list[str]:
    own = {
        "simplify", "cancel", "factor", "together", "expand_log",
        "trigsimp", "fraction", "integrate", "limit", "factorial",
        "solve", "nsolve", "pprint", "lambdify",
        "Poly", "PolynomialError",
    }
    return sorted(set(dir(_se)) | own)
