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

from sycan import cas as cas

from sycan.mna import Component, NoiseSource, NoiseSpec, StampContext, T, k_B

# Long-channel channel-thermal-noise excess factor (γ ≈ 2/3 in saturation).
_NOISE_GAMMA = cas.Rational(2, 3)


@dataclass
class _MOSFET_L1(Component):
    name: str
    drain: str
    gate: str
    source: str
    mu_n: cas.Expr
    Cox: cas.Expr
    W: cas.Expr
    L: cas.Expr
    V_TH: cas.Expr  # positive magnitude for both polarities
    lam: cas.Expr = field(default_factory=lambda: cas.Integer(0))
    C_gs: cas.Expr = field(default_factory=lambda: cas.Integer(0))
    C_gd: cas.Expr = field(default_factory=lambda: cas.Integer(0))
    V_GS_op: Optional[cas.Expr] = None
    V_DS_op: Optional[cas.Expr] = None
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
            self.V_GS_op = cas.Symbol(f"V_GS_op_{self.name}")
        if self.V_DS_op is None:
            self.V_DS_op = cas.Symbol(f"V_DS_op_{self.name}")
        for attr in (
            "mu_n", "Cox", "W", "L", "V_TH",
            "lam", "C_gs", "C_gd",
            "V_GS_op", "V_DS_op",
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
        return out

    @property
    def _pol(self) -> cas.Expr:
        return cas.Integer(1) if self.polarity == "N" else cas.Integer(-1)

    def _I_D_expr(self, V_GS: cas.Expr, V_DS: cas.Expr) -> cas.Expr:
        pol = self._pol
        V_GS_eff = pol * V_GS
        V_DS_eff = pol * V_DS
        V_ov = V_GS_eff - self.V_TH
        I_D_mag = (
            cas.Rational(1, 2)
            * self.mu_n * self.Cox * (self.W / self.L)
            * V_ov ** 2
            * (1 + self.lam * V_DS_eff)
        )
        return pol * I_D_mag

    # ------------------------------------------------------------------
    # Numeric helpers — operating region + DC drain current.
    #
    # The ``stamp_*`` methods above keep the model symbolic so the MNA
    # solver can do its symbolic work. These helpers evaluate the same
    # device equations on plain floats so quick bias-point sweeps
    # (parameter studies, browser demos, sanity checks) don't need a
    # full sympy solve. ``_to_float`` accepts either a Python number or
    # a sympy expression with all parameters already substituted.
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
        """Classify the device as ``cutoff``, ``triode`` or ``saturation``.

        The classification follows the standard long-channel rules:

        * ``V_GS_eff < V_TH``                       -> cutoff
        * ``V_GS_eff >= V_TH`` and ``V_DS_eff < V_GS_eff - V_TH`` -> triode
        * ``V_GS_eff >= V_TH`` and ``V_DS_eff >= V_GS_eff - V_TH`` -> saturation

        ``V_GS`` / ``V_DS`` are absolute (un-polarised) terminal-voltage
        differences ``V(g) - V(s)`` and ``V(d) - V(s)`` — the polarity
        flip for PMOS is applied internally so callers pass numbers
        straight from a simulation, regardless of device type.
        """
        pol = 1.0 if self.polarity == "N" else -1.0
        V_TH = self._to_float(self.V_TH)
        V_GS_eff = pol * self._to_float(V_GS)
        V_DS_eff = pol * self._to_float(V_DS)
        V_ov = V_GS_eff - V_TH
        if V_ov <= 0.0:
            return "cutoff"
        if V_DS_eff < V_ov:
            return "triode"
        return "saturation"

    def dc_current(self, V_GS, V_DS) -> float:
        """Drain current ``I_D`` (SPICE sign — positive *into* the drain).

        Region-aware: returns 0 in cutoff, the long-channel triode
        equation (with channel-length modulation) in triode, and the
        L1 saturation equation in saturation. Unlike :meth:`_I_D_expr`
        — which uses the saturation form everywhere because the
        symbolic AC stamps only need it at the operating point — this
        helper is intended for full-swing sweeps where the device
        actually crosses regions.
        """
        pol = 1.0 if self.polarity == "N" else -1.0
        V_TH = self._to_float(self.V_TH)
        V_GS_eff = pol * self._to_float(V_GS)
        V_DS_eff = pol * self._to_float(V_DS)
        V_ov = V_GS_eff - V_TH
        if V_ov <= 0.0:
            return 0.0
        beta = (
            self._to_float(self.mu_n)
            * self._to_float(self.Cox)
            * self._to_float(self.W) / self._to_float(self.L)
        )
        lam = self._to_float(self.lam)
        if V_DS_eff < V_ov:
            I_mag = beta * (V_ov * V_DS_eff - 0.5 * V_DS_eff ** 2) * (1.0 + lam * V_DS_eff)
        else:
            I_mag = 0.5 * beta * V_ov ** 2 * (1.0 + lam * V_DS_eff)
        return pol * I_mag

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


class NMOS_L1(_MOSFET_L1):
    """Shichman-Hodges Level 1 NMOS (saturation region)."""
    polarity: ClassVar[str] = "N"


class PMOS_L1(_MOSFET_L1):
    """Shichman-Hodges Level 1 PMOS (saturation region)."""
    polarity: ClassVar[str] = "P"
