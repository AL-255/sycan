"""Generic linear time-invariant transfer-function block.

Behavioural two-port that enforces

    V(out_p) - V(out_m) = H * (V(in_p) - V(in_m))

where ``H`` is an arbitrary sympy expression in ``var`` (default
``s``). Useful for signal-flow / loop-filter modelling — sigma-delta
modulators, behavioural filters, control-system blocks — where the
device-level realisation is irrelevant. The control inputs are
high-impedance (no current is drawn) and the output drives like an
ideal VCVS.

DC handling: ``H`` is evaluated at ``var = 0``. If the result is
finite the block stamps as a static VCVS with that DC gain; otherwise
the output is left open (no MNA contribution and no auxiliary row),
matching the way an ideal capacitor or integrator would behave at DC.
Pass ``dc_gain`` to override either choice.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Optional

from sycan import cas as cas

from sycan.mna import Component, NoiseSpec, StampContext


def _is_finite(expr: cas.Expr) -> bool:
    bad = (cas.zoo, cas.oo, -cas.oo, cas.nan)
    # Walk every sub-expression — works on any CAS backend that exposes
    # ``.args``. Symengine's ``.atoms()`` only enumerates Symbols, and
    # ``.has(cls)`` is sympy-only, so neither shortcut is portable here.
    stack = [expr]
    while stack:
        node = stack.pop()
        if node in bad:
            return False
        stack.extend(getattr(node, "args", ()))
    return True


@dataclass
class TransferFunction(Component):
    name: str
    in_p: str
    in_m: str
    out_p: str
    out_m: str
    H: cas.Expr
    var: cas.Symbol = field(default_factory=lambda: cas.Symbol("s"))
    dc_gain: Optional[cas.Expr] = None
    include_noise: NoiseSpec = field(default=None, kw_only=True)

    ports: ClassVar[tuple[str, ...]] = ("in_p", "in_m", "out_p", "out_m")
    has_aux: ClassVar[bool] = True
    SUPPORTED_NOISE: ClassVar[frozenset[str]] = frozenset()

    def __post_init__(self) -> None:
        self.H = cas.sympify(self.H)
        self.var = cas.sympify(self.var)
        if self.dc_gain is not None:
            self.dc_gain = cas.sympify(self.dc_gain)
        self.include_noise = self._normalize_noise(self.include_noise)

    def _dc_gain_or_none(self) -> Optional[cas.Expr]:
        if self.dc_gain is not None:
            return self.dc_gain
        g = self.H.subs(self.var, 0)
        return g if _is_finite(g) else None

    def aux_count(self, mode: str) -> int:
        if mode == "ac":
            return 1
        return 1 if self._dc_gain_or_none() is not None else 0

    def stamp(self, ctx: StampContext) -> None:
        if ctx.mode == "ac":
            gain = self.H.subs(self.var, ctx.s)
        else:
            gain = self._dc_gain_or_none()
            if gain is None:
                return

        aux = ctx.aux(self.name)
        i, j = ctx.n(self.out_p), ctx.n(self.out_m)
        ci, cj = ctx.n(self.in_p), ctx.n(self.in_m)
        if i >= 0:
            ctx.A[i, aux] += 1
            ctx.A[aux, i] += 1
        if j >= 0:
            ctx.A[j, aux] -= 1
            ctx.A[aux, j] -= 1
        if ci >= 0:
            ctx.A[aux, ci] -= gain
        if cj >= 0:
            ctx.A[aux, cj] += gain
