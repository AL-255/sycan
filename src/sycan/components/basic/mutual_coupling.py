"""Mutual inductance coupling (SPICE ``K``).

Couples two or more inductors with a coupling coefficient ``k``
(0 ≤ k ≤ 1).  In AC analysis stamps ``-s·M_ij`` into the auxiliary
rows for every pair (i, j), where ``M_ij = k · sqrt(L_i · L_j)``.

At DC the inductors are shorts so coupling has no effect.

Inductor values are resolved lazily during MNA assembly so that
a ``K`` element may appear earlier in the netlist than the ``L``
elements it references.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Optional, TYPE_CHECKING

from sycan import cas as cas

from sycan.mna import Component, NoiseSpec, StampContext

if TYPE_CHECKING:
    from sycan.components.basic.inductor import Inductor


@dataclass
class MutualCoupling(Component):
    """Mutual inductance coupling.

    Couples a set of inductors with coefficient ``k``.  Inductor values
    are resolved at MNA-build time from the flat component list, so
    ``K`` can precede the ``L`` elements it references.

    Parameters
    ----------
    name
        Designator (e.g. ``"K1"``).
    k
        Coupling coefficient (default 1).
    """

    name: str
    k: cas.Expr = field(default_factory=lambda: cas.Integer(1))
    include_noise: NoiseSpec = field(default=None, kw_only=True)

    _inductor_names: list[str] = field(
        default_factory=list, init=False, repr=False)
    _values: dict[str, cas.Expr] = field(
        default_factory=dict, init=False, repr=False)

    ports: ClassVar[tuple[str, ...]] = ()
    SUPPORTED_NOISE: ClassVar[frozenset[str]] = frozenset()

    def __post_init__(self) -> None:
        self.k = cas.sympify(self.k)
        self.include_noise = self._normalize_noise(self.include_noise)

    def couple(self, name: str, L_value: Optional[cas.Expr] = None) -> None:
        """Register a coupled inductor.

        If ``L_value`` is given the inductor value is locked immediately
        (Python API convenience path).  Otherwise the value is resolved
        lazily during :meth:`resolve` (SPICE-parser path, where ``K``
        may appear before ``L``).
        """
        self._inductor_names.append(name)
        if L_value is not None:
            self._values[name] = cas.sympify(L_value)

    def resolve(self, components: list[Component]) -> None:
        """Look up inductor values from a flat component list."""
        from sycan.components.basic.inductor import Inductor

        needed = [n for n in self._inductor_names if n not in self._values]
        if not needed:
            return  # all already resolved
        for c in components:
            if isinstance(c, Inductor) and c.name in needed:
                self._values[c.name] = c.value
                needed.remove(c.name)
        if needed:
            raise ValueError(
                f"MutualCoupling {self.name!r}: inductors not found: "
                f"{sorted(needed)!r}"
            )

    def stamp(self, ctx: StampContext) -> None:
        if ctx.mode != "ac":
            return
        if len(self._values) < 2:
            return
        s = ctx.s
        names = list(self._values.keys())
        for i in range(len(names)):
            ni = names[i]
            Li = self._values[ni]
            ai = ctx.aux(ni)
            for j in range(i + 1, len(names)):
                nj = names[j]
                Lj = self._values[nj]
                aj = ctx.aux(nj)
                M = self.k * cas.sqrt(Li * Lj)
                ctx.A[ai, aj] -= s * M
                ctx.A[aj, ai] -= s * M
