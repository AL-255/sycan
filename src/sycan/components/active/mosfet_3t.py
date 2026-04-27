"""Three-terminal segmented MOSFET — L1 strong inversion + matched
weak-inversion tail, polarity-aware (NMOS / PMOS).

The Shichman-Hodges Level 1 model in
:class:`~sycan.components.active.mosfet_l1.NMOS_L1` /
:class:`~sycan.components.active.mosfet_l1.PMOS_L1` predicts
``I_D = 0`` once the device crosses below threshold, which makes any
sub-threshold leakage invisible to the analyser. The standalone
weak-inversion exponential in
:class:`~sycan.components.active.mosfet_subthreshold.NMOS_subthreshold`
captures that leakage but blows up if you push it into strong inversion.

This module composes the two into a single segmented model:

* ``V_GS_eff >= V_off``  — Shichman-Hodges Level 1 (saturation + triode,
  with channel-length modulation).
* ``V_GS_eff <  V_off``  — exponential tail
  ``I_D = I_off * exp((V_GS_eff - V_off) / (m * V_T)) * (1 - exp(-V_DS_eff / V_T))``,
  whose prefactor and characteristic slope are *derived from* the L1
  parameters so the two pieces meet with continuous value AND continuous
  slope — i.e. C¹-smooth.

Derivation. Pick the strong/weak boundary at

    V_off = V_TH + 2 * m * V_T

(equivalent to ``V_OV = 2 m V_T`` — the standard SPICE/BSIM
matching point). At V_GS_eff = V_off the L1 saturation form gives

    I_L1     = (β / 2) * (2 m V_T)**2 = 2 β (m V_T)**2
    dI_L1    = β * (V_off - V_TH)     = 2 β m V_T

For the exponential tail
``I_sub = I_off * exp((V_GS_eff - V_off) / (m V_T))`` the value at the
boundary is ``I_off`` and the slope is ``I_off / (m V_T)``. Equating
both pairs gives ``I_off = 2 β (m V_T)**2`` *and* ``I_off / (m V_T) =
2 β m V_T`` — same answer, so the match is automatic. (Channel-length
modulation and V_DS shaping are added on top of that prefactor.)

The segment-aware DC current and operating-region helpers are exposed
as plain methods so quick numeric sweeps don't need a sympy solve.
``stamp_nonlinear`` assembles the same equations as a single
:class:`sympy.Piecewise` so the symbolic DC solver can also handle
the device cleanly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Optional

import sympy as sp

from sycan.mna import Component, NoiseSource, NoiseSpec, StampContext, T, k_B, q


# Reuse the same defaults as the standalone weak-inversion model so a
# parameter swap from MOSFET_subthreshold to MOSFET_3T doesn't subtly
# move the operating point.
_DEFAULT_VT = sp.Rational(2585, 100000)   # kT/q at ~300 K, in volts.
_DEFAULT_M  = sp.Rational(3, 2)           # long-channel slope factor.

# Channel-thermal-noise excess factor in saturation.
_NOISE_GAMMA = sp.Rational(2, 3)


@dataclass
class _MOSFET_3T(Component):
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
    m: sp.Expr = field(default_factory=lambda: _DEFAULT_M)
    V_T: sp.Expr = field(default_factory=lambda: _DEFAULT_VT)
    C_gs: sp.Expr = field(default_factory=lambda: sp.Integer(0))
    C_gd: sp.Expr = field(default_factory=lambda: sp.Integer(0))
    V_GS_op: Optional[sp.Expr] = None
    V_DS_op: Optional[sp.Expr] = None
    include_noise: NoiseSpec = field(default=None, kw_only=True)

    ports: ClassVar[tuple[str, ...]] = ("drain", "gate", "source")
    has_nonlinear: ClassVar[bool] = True
    polarity: ClassVar[str] = ""  # overridden by concrete subclasses
    SUPPORTED_NOISE: ClassVar[frozenset[str]] = frozenset({"thermal", "shot"})

    def __post_init__(self) -> None:
        if self.polarity not in ("N", "P"):
            raise TypeError(
                "_MOSFET_3T is abstract; instantiate NMOS_3T or PMOS_3T."
            )
        if self.V_GS_op is None:
            self.V_GS_op = sp.Symbol(f"V_GS_op_{self.name}")
        if self.V_DS_op is None:
            self.V_DS_op = sp.Symbol(f"V_DS_op_{self.name}")
        for attr in (
            "mu_n", "Cox", "W", "L", "V_TH",
            "lam", "m", "V_T",
            "C_gs", "C_gd",
            "V_GS_op", "V_DS_op",
        ):
            setattr(self, attr, sp.sympify(getattr(self, attr)))
        self.include_noise = self._normalize_noise(self.include_noise)

    # ------------------------------------------------------------------
    # Derived constants (the whole reason this class exists).
    # ------------------------------------------------------------------
    @property
    def _pol(self) -> sp.Expr:
        return sp.Integer(1) if self.polarity == "N" else sp.Integer(-1)

    @property
    def _beta(self) -> sp.Expr:
        return self.mu_n * self.Cox * (self.W / self.L)

    @property
    def V_off(self) -> sp.Expr:
        """Strong/weak-inversion split point (above V_TH by ``2 m V_T``)."""
        return self.V_TH + 2 * self.m * self.V_T

    @property
    def I_off(self) -> sp.Expr:
        """Drain current at the strong/weak boundary V_GS_eff = V_off."""
        return 2 * self._beta * (self.m * self.V_T) ** 2

    # ------------------------------------------------------------------
    # Numeric helpers — region classification + region-aware I_D.
    # ------------------------------------------------------------------
    @staticmethod
    def _to_float(v) -> float:
        if isinstance(v, (int, float)):
            return float(v)
        try:
            return float(sp.sympify(v))
        except (TypeError, ValueError) as exc:  # pragma: no cover — guard
            raise TypeError(
                f"cannot convert {v!r} to a float; substitute every "
                f"symbolic parameter before calling this helper"
            ) from exc

    def operating_region(self, V_GS, V_DS) -> str:
        """Classify the device into one of three segments.

        * ``"weak_inversion"`` — V_GS_eff < V_off (subthreshold tail).
        * ``"triode"``        — V_GS_eff >= V_off and V_DS_eff < V_GS_eff − V_TH.
        * ``"saturation"``    — V_GS_eff >= V_off and V_DS_eff >= V_GS_eff − V_TH.

        Note the strong-inversion entry happens at ``V_off`` (a couple
        ``m V_T`` above ``V_TH``), not at ``V_TH`` itself — that's the
        knee where the L1 quadratic and the matched exponential tail
        join. Below it the device is still nominally on, just decaying
        exponentially, which is how a real MOSFET behaves.
        """
        pol = 1.0 if self.polarity == "N" else -1.0
        V_TH = self._to_float(self.V_TH)
        m = self._to_float(self.m)
        V_T = self._to_float(self.V_T)
        V_off = V_TH + 2.0 * m * V_T
        V_GS_eff = pol * self._to_float(V_GS)
        V_DS_eff = pol * self._to_float(V_DS)
        if V_GS_eff < V_off:
            return "weak_inversion"
        if V_DS_eff < V_GS_eff - V_TH:
            return "triode"
        return "saturation"

    def dc_current(self, V_GS, V_DS) -> float:
        """Drain current ``I_D`` (SPICE sign — positive *into* the drain).

        Region-aware: long-channel L1 in strong inversion, the
        matched-exponential tail in weak inversion.
        """
        pol = 1.0 if self.polarity == "N" else -1.0
        V_TH = self._to_float(self.V_TH)
        m = self._to_float(self.m)
        V_T = self._to_float(self.V_T)
        lam = self._to_float(self.lam)
        beta = (
            self._to_float(self.mu_n)
            * self._to_float(self.Cox)
            * self._to_float(self.W) / self._to_float(self.L)
        )
        V_off = V_TH + 2.0 * m * V_T
        I_off_val = 2.0 * beta * (m * V_T) ** 2
        V_GS_eff = pol * self._to_float(V_GS)
        V_DS_eff = pol * self._to_float(V_DS)
        if V_GS_eff < V_off:
            # Weak-inversion tail. The (1 - exp(-V_DS_eff/V_T)) factor
            # makes the current vanish at V_DS = 0 (KCL-clean) and
            # saturate above a few V_T, mirroring the standalone
            # subthreshold model.
            import math
            mag = (
                I_off_val
                * math.exp((V_GS_eff - V_off) / (m * V_T))
                * (1.0 - math.exp(-V_DS_eff / V_T))
                * (1.0 + lam * V_DS_eff)
            )
            return pol * mag
        # Strong inversion.
        V_ov = V_GS_eff - V_TH
        if V_DS_eff < V_ov:
            mag = beta * (V_ov * V_DS_eff - 0.5 * V_DS_eff ** 2) * (1.0 + lam * V_DS_eff)
        else:
            mag = 0.5 * beta * V_ov ** 2 * (1.0 + lam * V_DS_eff)
        return pol * mag

    # ------------------------------------------------------------------
    # Symbolic DC current — the same Piecewise the solver / AC stamps use.
    # ------------------------------------------------------------------
    def _I_D_expr(self, V_GS: sp.Expr, V_DS: sp.Expr) -> sp.Expr:
        pol = self._pol
        V_GS_eff = pol * V_GS
        V_DS_eff = pol * V_DS
        beta = self._beta
        V_off = self.V_off
        I_off = self.I_off
        V_ov = V_GS_eff - self.V_TH

        I_sat = sp.Rational(1, 2) * beta * V_ov ** 2 * (1 + self.lam * V_DS_eff)
        I_tri = (
            beta * (V_ov * V_DS_eff - sp.Rational(1, 2) * V_DS_eff ** 2)
            * (1 + self.lam * V_DS_eff)
        )
        # Standard textbook weak-inversion shape factor — saturates at 1
        # for V_DS_eff > a few V_T and vanishes at V_DS = 0. Kept in its
        # plain form (no ``sp.Abs`` antisymmetric extension) so the
        # symbolic Jacobian produced by sympy's lambdify path stays
        # tractable for the numeric Newton fallback in ``solve_dc``.
        # Physical operation always has V_DS_eff ≥ 0 (the polarity flip
        # for PMOS handles the sign), so the asymmetric form is fine.
        shape = 1 - sp.exp(-V_DS_eff / self.V_T)
        I_weak = (
            I_off
            * sp.exp((V_GS_eff - V_off) / (self.m * self.V_T))
            * shape
            * (1 + self.lam * V_DS_eff)
        )

        I_strong = sp.Piecewise(
            (I_tri, V_DS_eff < V_ov),
            (I_sat, True),
        )
        I_mag = sp.Piecewise(
            (I_weak, V_GS_eff < V_off),
            (I_strong, True),
        )
        return pol * I_mag

    def _small_signal_params(self) -> tuple[sp.Expr, sp.Expr]:
        _vgs, _vds = sp.Dummy("vgs"), sp.Dummy("vds")
        I_D = self._I_D_expr(_vgs, _vds)
        sub = {_vgs: self.V_GS_op, _vds: self.V_DS_op}
        g_m = sp.diff(I_D, _vgs).subs(sub)
        g_ds = sp.diff(I_D, _vds).subs(sub)
        return g_m, g_ds

    # ------------------------------------------------------------------
    # MNA stamps.
    # ------------------------------------------------------------------
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
        if "shot" in self.include_noise:
            # In weak inversion the channel current is shot-noise-like
            # (single-carrier injection). Magnitude is set by I_off, the
            # current at the boundary — simulators usually parameterise
            # this on the actual operating-point current.
            out.append(
                NoiseSource(
                    name=f"{self.name}.shot",
                    kind="shot",
                    n_plus=self.drain,
                    n_minus=self.source,
                    psd=2 * q * self.I_off,
                )
            )
        return out

    def stamp(self, ctx: StampContext) -> None:
        if ctx.mode != "ac":
            return
        g_m, g_ds = self._small_signal_params()
        s = ctx.s
        d = ctx.n(self.drain)
        g = ctx.n(self.gate)
        src = ctx.n(self.source)

        if d >= 0:
            if g >= 0:
                ctx.A[d, g] += g_m
            if src >= 0:
                ctx.A[d, src] -= g_m
        if src >= 0:
            if g >= 0:
                ctx.A[src, g] -= g_m
            ctx.A[src, src] += g_m

        if d >= 0:
            ctx.A[d, d] += g_ds
        if src >= 0:
            ctx.A[src, src] += g_ds
        if d >= 0 and src >= 0:
            ctx.A[d, src] -= g_ds
            ctx.A[src, d] -= g_ds

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


class NMOS_3T(_MOSFET_3T):
    """Segmented L1 + weak-inversion NMOS (three-terminal, bulk-tied)."""
    polarity: ClassVar[str] = "N"


class PMOS_3T(_MOSFET_3T):
    """Segmented L1 + weak-inversion PMOS (three-terminal, bulk-tied;
    ``V_TH`` is a positive magnitude)."""
    polarity: ClassVar[str] = "P"
