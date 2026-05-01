"""sycan.cas backend that forwards every attribute to :mod:`sympy`.

The wrapper module deliberately does *not* enumerate sympy's public
surface — that would require chasing every release. Instead a
module-level ``__getattr__`` forwards attribute access to the underlying
sympy module, so callers see the same names sympy itself exposes.
"""
from __future__ import annotations

from typing import Any

import sympy as _sympy


def __getattr__(name: str) -> Any:
    if name.startswith("__") and name.endswith("__"):
        raise AttributeError(name)
    return getattr(_sympy, name)


def __dir__() -> list[str]:
    return dir(_sympy)
