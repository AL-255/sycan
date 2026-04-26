"""Component model packages.

On import, every subpackage is walked and every module imported so that
each :class:`~sycan.mna.Component` subclass registers itself into
``Component._registry`` via ``__init_subclass__``. The result — a name
→ class mapping of every component the program knows how to build — is
exposed via :func:`available_components`.
"""
from __future__ import annotations

import importlib
import pkgutil

from sycan.mna import Component


def _discover() -> None:
    """Import every module under this package so subclasses register.

    Walks subpackages recursively. Modules with names starting with ``_``
    (private helpers) are skipped.
    """
    package = importlib.import_module(__name__)
    for mod in pkgutil.walk_packages(package.__path__, prefix=f"{__name__}."):
        leaf = mod.name.rsplit(".", 1)[-1]
        if leaf.startswith("_"):
            continue
        importlib.import_module(mod.name)


def available_components() -> dict[str, type[Component]]:
    """Return the registry of every concrete Component subclass."""
    return Component.available()


_discover()
