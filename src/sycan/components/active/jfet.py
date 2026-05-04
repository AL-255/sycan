"""Shichman-Hodges JFET, polarity-aware (N-JFET / P-JFET).

The JFET is a depletion-mode device: the channel is fully open at
``V_GS = 0`` and pinches off when the gate-source voltage is
sufficiently reverse-biased. With

    pol       = +1 for NJFET, -1 for PJFET
    V_GS_eff  = pol * (V(gate)   - V(source))
    V_DS_eff  = pol * (V(drain)  - V(source))
    V_ov      = V_GS_eff + VTO

the conduction condition in both cases is ``V_ov > 0`` and saturation
is ``V_DS_eff >= V_ov``. ``VTO`` is stored as a positive magnitude
for both polarities (analogous to ``V_TH`` for MOSFETs).

**DC drain current (saturation + channel-length modulation)**::

    I_D_mag   = BETA * (V_GS_eff + VTO)**2 * (1 + LAMBDA * V_DS_eff)
    I_D_SPICE = pol * I_D_mag     # current INTO drain (SPICE sign)

For NJFET this is positive (I_D flows into drain); for PJFET it is
negative (the device pulls current out of the drain externally).

**AC small-signal model** — obtained by differentiating ``I_D_SPICE``
at ``(V_GS_op, V_DS_op)``::

    g_m  = dI_D/dV_GS|_OP
    g_ds = dI_D/dV_DS|_OP

Gate capacitances ``C_gs`` / ``C_gd`` are stamped as ``s*C``
admittances in AC mode. The gate-channel junction is assumed to be
ideal (zero gate current) — simple Shichman-Hodges model.

Concrete classes :class:`NJFET` and :class:`PJFET` fix
``polarity`` via :data:`typing.ClassVar`; users pick one in their
netlist / Python API.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Optional

from sycan import cas as cas

from sycan.mna import Component, NoiseSource, NoiseSpec, StampContext, T, k_B, freq

# Long-channel channel-thermal-noise excess factor (γ ≈ 2/3 in saturation).
_NOISE_GAMMA = cas.Rational(2, 3)


@dataclass
class _JFET(Component):
    name: str
    drain: str
    gate: str
    source: str
    BETA: cas.Expr
    VTO: cas.Expr  # positive magnitude for both polarities
    LAMBDA: cas.Expr = field(default_factory=lambda: cas.Integer(0))
    C_gs: cas.Expr = field(default_factory=lambda: cas.Integer(0))
    C_gd: cas.Expr = field(default_factory=lambda: cas.Integer(0))
    V_GS_op: Optional[cas.Expr] = None
    V_DS_op: Optional[cas.Expr] = None
    KF: cas.Expr = field(default_factory=lambda: cas.Integer(0))
    AF: cas.Expr = field(default_factory=lambda: cas.Integer(1))
    EF: cas.Expr = field(default_factory=lambda: cas.Integer(1))
    include_noise: NoiseSpec = field(default=None, kw_only=True)

    ports: ClassVar[tuple[str, ...]] = ("drain", "gate", "source")
    has_nonlinear: ClassVar[bool] = True
    polarity: ClassVar[str] = ""  # overridden by concrete subclasses
    SUPPORTED_NOISE: ClassVar[frozenset[str]] = frozenset({"thermal", "flicker"})

    def __post_init__(self) -> None:
        if self.polarity not in ("N", "P"):
            raise TypeError(
                "_JFET is abstract; instantiate NJFET or PJFET."
            )
        if self.V_GS_op is None:
            self.V_GS_op = cas.Symbol(f"V_GS_op_{self.name}")
        if self.V_DS_op is None:
            self.V_DS_op = cas.Symbol(f"V_DS_op_{self.name}")
        for attr in (
            "BETA", "VTO", "LAMBDA", "C_gs", "C_gd",
            "V_GS_op", "V_DS_op",
            "KF", "AF", "EF",
        ):
            setattr(self, attr, cas.sympify(getattr(self, attr)))
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
        if "flicker" in self.include_noise and self.KF != 0:
            _, g_ds = self._small_signal_params()
            I_op = g_ds * self.V_DS_op if self.V_DS_op != 0 else g_ds
            out.append(
                NoiseSource(
                    name=f"{self.name}.flicker",
                    kind="flicker",
                    n_plus=self.drain,
                    n_minus=self.source,
                    psd=self.KF * I_op ** self.AF / (freq ** self.EF),
                )
            )
        return out

    @property
    def _pol(self) -> cas.Expr:
        return cas.Integer(1) if self.polarity == "N" else cas.Integer(-1)

    def _I_D_expr(self, V_GS: cas.Expr, V_DS: cas.Expr) -> cas.Expr:
        pol = self._pol
        V_GS_eff = pol * V_GS
        V_DS_eff = pol * V_DS
        V_ov = V_GS_eff + self.VTO
        I_D_mag = (
            self.BETA
            * V_ov ** 2
            * (1 + self.LAMBDA * V_DS_eff)
        )
        return pol * I_D_mag

    def _small_signal_params(self) -> tuple[cas.Expr, cas.Expr]:
        _vgs, _vds = cas.Dummy("vgs"), cas.Dummy("vds")
        I_D = self._I_D_expr(_vgs, _vds)
        sub = {_vgs: self.V_GS_op, _vds: self.V_DS_op}
        g_m = cas.diff(I_D, _vgs).subs(sub)
        g_ds = cas.diff(I_D, _vds).subs(sub)
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

        V_d = ctx.x[d] if d >= 0 else cas.Integer(0)
        V_g = ctx.x[g] if g >= 0 else cas.Integer(0)
        V_s = ctx.x[src] if src >= 0 else cas.Integer(0)

        I_D = self._I_D_expr(V_g - V_s, V_d - V_s)
        if d >= 0:
            ctx.residuals[d] += I_D
        if src >= 0:
            ctx.residuals[src] -= I_D

    # ------------------------------------------------------------------
    # Numeric helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _to_float(v) -> float:
        if isinstance(v, (int, float)):
            return float(v)
        try:
            return float(cas.sympify(v))
        except (TypeError, ValueError) as exc:  # pragma: no cover — guard
            raise TypeError(
                f"cannot convert {v!r} to a float; substitute every "
                f"symbolic parameter before calling this helper"
            ) from exc

    def operating_region(self, V_GS, V_DS) -> str:
        pol = 1.0 if self.polarity == "N" else -1.0
        VTO = self._to_float(self.VTO)
        V_GS_eff = pol * self._to_float(V_GS)
        V_DS_eff = pol * self._to_float(V_DS)
        V_ov = V_GS_eff + VTO
        if V_ov <= 0.0:
            return "cutoff"
        if V_DS_eff < V_ov:
            return "triode"
        return "saturation"

    def dc_current(self, V_GS, V_DS) -> float:
        pol = 1.0 if self.polarity == "N" else -1.0
        VTO = self._to_float(self.VTO)
        V_GS_eff = pol * self._to_float(V_GS)
        V_DS_eff = pol * self._to_float(V_DS)
        V_ov = V_GS_eff + VTO
        if V_ov <= 0.0:
            return 0.0
        BETA = self._to_float(self.BETA)
        LAMBDA = self._to_float(self.LAMBDA)
        if V_DS_eff < V_ov:
            I_mag = BETA * (2.0 * V_ov * V_DS_eff - V_DS_eff ** 2) * (1.0 + LAMBDA * V_DS_eff)
        else:
            I_mag = BETA * V_ov ** 2 * (1.0 + LAMBDA * V_DS_eff)
        return pol * I_mag


class NJFET(_JFET):
    """Shichman-Hodges N-channel JFET (depletion-mode)."""
    polarity: ClassVar[str] = "N"


class PJFET(_JFET):
    """Shichman-Hodges P-channel JFET (depletion-mode)."""
    polarity: ClassVar[str] = "P"
