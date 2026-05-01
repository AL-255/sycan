"""Weighted summing junction.

Enforces

    V(out_p) - V(out_m) = sum_k  w_k * (V(in_p_k) - V(in_m_k))

over an arbitrary list of differential inputs supplied as
``(in_p, in_m, weight)`` tuples (or 2-tuples ``(node, weight)`` for
inputs referenced to ground).

Inputs are high-impedance — no current is drawn from any of the
control nodes — and the output is voltage-forced through a single
auxiliary branch current. This is the building block that produces the
``error = input - feedback`` node at the front of every sigma-delta
loop and the inter-stage summers in CIFB / CIFF cascades.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Iterator

from sycan import cas as cas

from sycan.mna import Component, NoiseSpec, StampContext


@dataclass
class Summer(Component):
    name: str
    out_p: str
    out_m: str
    inputs: list  # list of (in_p, in_m, weight) or (node, weight)
    include_noise: NoiseSpec = field(default=None, kw_only=True)

    ports: ClassVar[tuple[str, ...]] = ("out_p", "out_m")
    has_aux: ClassVar[bool] = True
    SUPPORTED_NOISE: ClassVar[frozenset[str]] = frozenset()

    def __post_init__(self) -> None:
        normalised: list[tuple[str, str, cas.Expr]] = []
        for entry in self.inputs:
            if len(entry) == 2:
                node, w = entry
                normalised.append((node, "0", cas.sympify(w)))
            elif len(entry) == 3:
                p, m, w = entry
                normalised.append((p, m, cas.sympify(w)))
            else:
                raise ValueError(
                    f"{self.name}: each summer input must be a 2- or 3-tuple, "
                    f"got {entry!r}"
                )
        self.inputs = normalised
        # Per-input attributes ``in0_p``/``in0_m``/``in1_p``/... — these
        # let helpers that walk components via ``getattr`` (e.g.
        # autodraw's port resolver) reach each input net by name. They
        # mirror ``self.inputs`` and are kept in sync only at
        # construction time; mutating ``self.inputs`` afterwards is not
        # supported.
        for i, (p, m, _w) in enumerate(self.inputs):
            setattr(self, f"in{i}_p", p)
            setattr(self, f"in{i}_m", m)
        self.include_noise = self._normalize_noise(self.include_noise)

    def iter_node_names(self) -> Iterator[str]:
        yield self.out_p
        yield self.out_m
        for p, m, _w in self.inputs:
            yield p
            yield m

    def stamp(self, ctx: StampContext) -> None:
        aux = ctx.aux(self.name)
        i, j = ctx.n(self.out_p), ctx.n(self.out_m)
        if i >= 0:
            ctx.A[i, aux] += 1
            ctx.A[aux, i] += 1
        if j >= 0:
            ctx.A[j, aux] -= 1
            ctx.A[aux, j] -= 1
        for p, m, w in self.inputs:
            ci, cj = ctx.n(p), ctx.n(m)
            if ci >= 0:
                ctx.A[aux, ci] -= w
            if cj >= 0:
                ctx.A[aux, cj] += w
