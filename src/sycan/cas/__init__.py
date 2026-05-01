"""sycan.cas — pluggable CAS backend.

Sycan keeps every symbolic primitive — :class:`Symbol`, :class:`Matrix`,
:func:`solve`, :func:`simplify`, etc. — behind this thin proxy module so
that the underlying CAS library can be swapped without touching the rest
of the codebase. Today only :data:`"sympy"` is wired up; future backends
can be added by writing another ``_<name>_backend.py`` and extending
:func:`select_backend`.

Typical use::

    from sycan import cas as cas
    x = cas.Symbol("x")
    cas.solve(cas.Eq(x**2, 4), x)

To force a specific backend (otherwise ``"sympy"`` is selected on first
attribute access)::

    from sycan import cas
    cas.select_backend("sympy")

The proxy forwards ``getattr`` to the active backend module, so any name
the backend exposes is reachable directly — both as attribute access
(``cas.Matrix``) and as ``from sycan.cas import Matrix``.
"""
from __future__ import annotations

import importlib
import os
from types import ModuleType
from typing import Any, Optional

_BACKEND: Optional[ModuleType] = None
_BACKEND_NAME: Optional[str] = None

_AVAILABLE_BACKENDS: tuple[str, ...] = ("sympy", "symengine")

# Environment variable that lets users (and pytest) choose the backend
# *before* the rest of sycan is imported. Module-level constants such as
# ``_NOISE_GAMMA = cas.Rational(2, 3)`` are evaluated at import time, so
# the backend has to be locked in before ``import sycan`` happens —
# otherwise the constants pick up the default sympy backend and any
# later switch gives a mixed-type expression that defeats the purpose.
_BACKEND_ENV_VAR = "SYCAN_CAS_BACKEND"

# Map ``select_backend`` name -> dotted module path of the backend
# implementation. Adding a new backend means writing ``_<name>_backend.py``
# in this package and adding an entry here.
_BACKEND_MODULES: dict[str, str] = {
    "sympy": "sycan.cas._sympy_backend",
    "symengine": "sycan.cas._symengine_backend",
}


def select_backend(name: str) -> None:
    """Choose the active CAS backend.

    Parameters
    ----------
    name
        Backend identifier. Currently supported values are ``"sympy"``
        (default, full coverage) and ``"symengine"`` (fast C++ core;
        ``simplify`` / ``solve`` / ``factor`` and friends bridge to
        sympy under the hood, the rest runs natively in symengine).
    """
    global _BACKEND, _BACKEND_NAME
    try:
        module_path = _BACKEND_MODULES[name]
    except KeyError:
        raise ValueError(
            f"unknown CAS backend {name!r}; "
            f"available: {list(_AVAILABLE_BACKENDS)!r}"
        ) from None
    # Use importlib so we never re-enter our own ``__getattr__`` via
    # ``from sycan.cas import _sympy_backend`` during bootstrap.
    _BACKEND = importlib.import_module(module_path)
    _BACKEND_NAME = name


def backend_name() -> str:
    """Return the active backend's name (selecting the default if needed)."""
    if _BACKEND_NAME is None:
        select_backend("sympy")
    assert _BACKEND_NAME is not None
    return _BACKEND_NAME


def available_backends() -> tuple[str, ...]:
    """Return the tuple of backend names recognised by :func:`select_backend`."""
    return _AVAILABLE_BACKENDS


def _ensure_backend() -> ModuleType:
    if _BACKEND is None:
        select_backend("sympy")
    assert _BACKEND is not None
    return _BACKEND


# PEP 562 module-level __getattr__: forwards every unknown attribute to
# the active backend module. The backend module in turn forwards to its
# CAS library, so the entire public API of the chosen library is reachable
# through the proxy without enumerating names here.
def __getattr__(name: str) -> Any:
    # Skip private / dunder attributes — they belong to the proxy module
    # itself (or to Python's import machinery, e.g. ``_sympy_backend``)
    # and must never be forwarded, otherwise the bootstrap recurses.
    if name.startswith("_"):
        raise AttributeError(name)
    backend = _ensure_backend()
    try:
        return getattr(backend, name)
    except AttributeError as exc:
        raise AttributeError(
            f"CAS backend {_BACKEND_NAME!r} exposes no attribute {name!r}"
        ) from exc


def __dir__() -> list[str]:
    backend = _ensure_backend()
    return sorted(
        set(dir(backend))
        | {"select_backend", "backend_name", "available_backends"}
    )


# Eager backend selection at package import. If the env var is unset the
# default sympy backend is selected on first attribute access; if it's
# set, lock the choice in *now* so any sycan module-level constants
# created by subsequent imports are minted in the right CAS.
_env_backend = os.environ.get(_BACKEND_ENV_VAR)
if _env_backend:
    select_backend(_env_backend)
