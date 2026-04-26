"""Shichman-Hodges Level 1 MOSFET, polarity-aware (NMOS / PMOS).

The model equations below are written in "effective" form. With

    pol       = +1 for NMOS, -1 for PMOS
    V_GS_eff  = pol * (V(gate)   - V(source))
    V_DS_eff  = pol * (V(drain)  - V(source))

the conduction condition in both cases is ``V_GS_eff > V_TH`` and
``V_DS_eff >= V_GS_eff - V_TH``. The threshold ``V_TH`` is stored as
a positive magnitude for both polarities.

**DC drain current (saturation + channel-length modulation)**::

    I_D_mag   = (1/2) * mu_n * Cox * (W/L)
              * (V_GS_eff - V_TH)**2 * (1 + lam * V_DS_eff)
    I_D_SPICE = pol * I_D_mag     # current INTO drain (SPICE sign)

For NMOS this is the usual positive I_D; for PMOS it is negative (the
device pulls current out of the drain externally).

**AC small-signal model** — obtained by differentiating ``I_D_SPICE``
at ``(V_GS_op, V_DS_op)`` with :func:`sympy.diff`::

    g_m  = dI_D/dV_GS|_OP
    g_ds = dI_D/dV_DS|_OP

Gate capacitances ``C_gs`` / ``C_gd`` are stamped as ``s*C``
admittances in AC mode. Bulk is tied to source (three-terminal).

Concrete classes :class:`NMOS_L1` and :class:`PMOS_L1` fix
``polarity`` via :data:`typing.ClassVar`; users pick one in their
netlist / Python API.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Optional

import sympy as sp

from sycan.mna import Component, NoiseSource, NoiseSpec, StampContext, T, k_B

# Long-channel channel-thermal-noise excess factor (γ ≈ 2/3 in saturation).
_NOISE_GAMMA = sp.Rational(2, 3)


@dataclass
class _MOSFET_L1(Component):
    name: str
    drain: str
    gate: str
    source: str
    mu_n: sp.Expr
    Cox: sp.Expr
    W: sp.Expr
    L: sp.Expr
    V_TH: sp.Expr  # positive magnitude for both polarities
    lam: sp.Expr = field(default_factory=lambda: sp.Integer(0))
    C_gs: sp.Expr = field(default_factory=lambda: sp.Integer(0))
    C_gd: sp.Expr = field(default_factory=lambda: sp.Integer(0))
    V_GS_op: Optional[sp.Expr] = None
    V_DS_op: Optional[sp.Expr] = None
    include_noise: NoiseSpec = field(default=None, kw_only=True)

    ports: ClassVar[tuple[str, ...]] = ("drain", "gate", "source")
    has_nonlinear: ClassVar[bool] = True
    polarity: ClassVar[str] = ""  # overridden by concrete subclasses
    SUPPORTED_NOISE: ClassVar[frozenset[str]] = frozenset({"thermal"})

    def __post_init__(self) -> None:
        if self.polarity not in ("N", "P"):
            raise TypeError(
                "_MOSFET_L1 is abstract; instantiate NMOS_L1 or PMOS_L1."
            )
        if self.V_GS_op is None:
            self.V_GS_op = sp.Symbol(f"V_GS_op_{self.name}")
        if self.V_DS_op is None:
            self.V_DS_op = sp.Symbol(f"V_DS_op_{self.name}")
        for attr in (
            "mu_n", "Cox", "W", "L", "V_TH",
            "lam", "C_gs", "C_gd",
            "V_GS_op", "V_DS_op",
        ):
            setattr(self, attr, sp.sympify(getattr(self, attr)))
        self.include_noise = self._normalize_noise(self.include_noise)

    def noise_sources(self) -> list[NoiseSource]:
        out: list[NoiseSource] = []
        if "thermal" in self.include_noise:
            g_m, _ = self._small_signal_params()
            out.append(
                NoiseSource(
                    name=f"{self.name}.thermal",
                    kind="thermal",
                    n_plus=self.drain,
                    n_minus=self.source,
                    psd=4 * k_B * T * _NOISE_GAMMA * g_m,
                )
            )
        return out

    @property
    def _pol(self) -> sp.Expr:
        return sp.Integer(1) if self.polarity == "N" else sp.Integer(-1)

    def _I_D_expr(self, V_GS: sp.Expr, V_DS: sp.Expr) -> sp.Expr:
        pol = self._pol
        V_GS_eff = pol * V_GS
        V_DS_eff = pol * V_DS
        V_ov = V_GS_eff - self.V_TH
        I_D_mag = (
            sp.Rational(1, 2)
            * self.mu_n * self.Cox * (self.W / self.L)
            * V_ov ** 2
            * (1 + self.lam * V_DS_eff)
        )
        return pol * I_D_mag

    def _small_signal_params(self) -> tuple[sp.Expr, sp.Expr]:
        _vgs, _vds = sp.Dummy("vgs"), sp.Dummy("vds")
        I_D = self._I_D_expr(_vgs, _vds)
        sub = {_vgs: self.V_GS_op, _vds: self.V_DS_op}
        g_m = sp.diff(I_D, _vgs).subs(sub)
        g_ds = sp.diff(I_D, _vds).subs(sub)
        return g_m, g_ds

    def stamp(self, ctx: StampContext) -> None:
        if ctx.mode != "ac":
            return

        g_m, g_ds = self._small_signal_params()
        s = ctx.s
        d = ctx.n(self.drain)
        g = ctx.n(self.gate)
        src = ctx.n(self.source)

        # VCCS g_m * (V(g) - V(s)) from drain -> source (internally).
        if d >= 0:
            if g >= 0:
                ctx.A[d, g] += g_m
            if src >= 0:
                ctx.A[d, src] -= g_m
        if src >= 0:
            if g >= 0:
                ctx.A[src, g] -= g_m
            ctx.A[src, src] += g_m

        # Output conductance g_ds between drain and source.
        if d >= 0:
            ctx.A[d, d] += g_ds
        if src >= 0:
            ctx.A[src, src] += g_ds
        if d >= 0 and src >= 0:
            ctx.A[d, src] -= g_ds
            ctx.A[src, d] -= g_ds

        # Intrinsic capacitances (admittance s*C).
        for c_value, p_a, p_b in ((self.C_gs, g, src), (self.C_gd, g, d)):
            Y = s * c_value
            if p_a >= 0:
                ctx.A[p_a, p_a] += Y
            if p_b >= 0:
                ctx.A[p_b, p_b] += Y
            if p_a >= 0 and p_b >= 0:
                ctx.A[p_a, p_b] -= Y
                ctx.A[p_b, p_a] -= Y

    def stamp_nonlinear(self, ctx: StampContext) -> None:
        if ctx.mode != "dc":
            return
        assert ctx.x is not None and ctx.residuals is not None

        d = ctx.n(self.drain)
        g = ctx.n(self.gate)
        src = ctx.n(self.source)

        V_d = ctx.x[d] if d >= 0 else sp.Integer(0)
        V_g = ctx.x[g] if g >= 0 else sp.Integer(0)
        V_s = ctx.x[src] if src >= 0 else sp.Integer(0)

        I_D = self._I_D_expr(V_g - V_s, V_d - V_s)
        if d >= 0:
            ctx.residuals[d] += I_D
        if src >= 0:
            ctx.residuals[src] -= I_D


class NMOS_L1(_MOSFET_L1):
    """Shichman-Hodges Level 1 NMOS (saturation region)."""
    polarity: ClassVar[str] = "N"


class PMOS_L1(_MOSFET_L1):
    """Shichman-Hodges Level 1 PMOS (saturation region)."""
    polarity: ClassVar[str] = "P"
