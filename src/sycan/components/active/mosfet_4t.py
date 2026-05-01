"""Four-terminal segmented MOSFET — L1 strong inversion + matched
weak-inversion tail, polarity-aware (NMOS / PMOS), with the bulk node
exposed and a long-channel body-effect correction on the threshold.

This is the canonical implementation; the three-terminal cell
:class:`~sycan.components.active.mosfet_3t.NMOS_3T` /
:class:`~sycan.components.active.mosfet_3t.PMOS_3T` is a thin
wrapper that ties the bulk to the source so the body effect drops out.

Equations
---------
Threshold with body effect (long-channel form)::

    V_TH(V_SB) = V_TH0 + γ * (sqrt(2 φ_F + V_SB) − sqrt(2 φ_F))

with ``V_SB`` the polarity-aware source-to-bulk voltage,
``γ = gamma`` the body-effect coefficient (units √V), and ``2 φ_F = phi``
the surface potential at strong inversion (default 0.7 V). Setting
``gamma = 0`` (the default) makes the body terminal cosmetic — it
still appears as a port, but it does not feed back into ``V_TH``.

Drain current uses the same segmented form as ``MOSFET_3T``:

* ``V_GS_eff >= V_off``  — Shichman-Hodges Level 1 (saturation +
  triode, with optional channel-length modulation ``λ``).
* ``V_GS_eff <  V_off``  — exponential tail joined C¹-smooth at the
  boundary ``V_off = V_TH + 2 m V_T`` with prefactor
  ``I_off = 2 β (m V_T)²``.

The body effect enters through ``V_TH``, so the strong/weak boundary,
the L1 saturation knee, and the weak-inversion exponential offset all
shift consistently with ``V_SB``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Optional

from sycan import cas as cas

from sycan.mna import Component, NoiseSource, NoiseSpec, StampContext, T, k_B, q


# Reuse the same defaults as the standalone weak-inversion model so a
# parameter swap doesn't subtly move the operating point.
_DEFAULT_VT  = cas.Rational(2585, 100000)   # kT/q at ~300 K, in volts.
_DEFAULT_M   = cas.Rational(3, 2)           # long-channel slope factor.
_DEFAULT_PHI = cas.Rational(7, 10)          # 2 φ_F default ≈ 0.7 V.

# Channel-thermal-noise excess factor in saturation.
_NOISE_GAMMA = cas.Rational(2, 3)


@dataclass
class _MOSFET_4T(Component):
    name: str
    drain: str
    gate: str
    source: str
    bulk: str
    mu_n: cas.Expr
    Cox: cas.Expr
    W: cas.Expr
    L: cas.Expr
    V_TH0: cas.Expr  # zero-bias threshold (positive magnitude)
    lam: cas.Expr = field(default_factory=lambda: cas.Integer(0))
    gamma: cas.Expr = field(default_factory=lambda: cas.Integer(0))
    phi: cas.Expr = field(default_factory=lambda: _DEFAULT_PHI)
    m: cas.Expr = field(default_factory=lambda: _DEFAULT_M)
    V_T: cas.Expr = field(default_factory=lambda: _DEFAULT_VT)
    C_gs: cas.Expr = field(default_factory=lambda: cas.Integer(0))
    C_gd: cas.Expr = field(default_factory=lambda: cas.Integer(0))
    V_GS_op: Optional[cas.Expr] = None
    V_DS_op: Optional[cas.Expr] = None
    V_BS_op: Optional[cas.Expr] = None
    include_noise: NoiseSpec = field(default=None, kw_only=True)

    ports: ClassVar[tuple[str, ...]] = ("drain", "gate", "source", "bulk")
    has_nonlinear: ClassVar[bool] = True
    polarity: ClassVar[str] = ""  # overridden by concrete subclasses
    SUPPORTED_NOISE: ClassVar[frozenset[str]] = frozenset({"thermal", "shot"})

    def __post_init__(self) -> None:
        if self.polarity not in ("N", "P"):
            raise TypeError(
                "_MOSFET_4T is abstract; instantiate NMOS_4T or PMOS_4T."
            )
        if self.V_GS_op is None:
            self.V_GS_op = cas.Symbol(f"V_GS_op_{self.name}")
        if self.V_DS_op is None:
            self.V_DS_op = cas.Symbol(f"V_DS_op_{self.name}")
        if self.V_BS_op is None:
            self.V_BS_op = cas.Symbol(f"V_BS_op_{self.name}")
        for attr in (
            "mu_n", "Cox", "W", "L", "V_TH0",
            "lam", "gamma", "phi", "m", "V_T",
            "C_gs", "C_gd",
            "V_GS_op", "V_DS_op", "V_BS_op",
        ):
            setattr(self, attr, cas.sympify(getattr(self, attr)))
        self.include_noise = self._normalize_noise(self.include_noise)

    # ------------------------------------------------------------------
    # Derived quantities — these now depend on V_SB through V_TH.
    # ------------------------------------------------------------------
    @property
    def _pol(self) -> cas.Expr:
        return cas.Integer(1) if self.polarity == "N" else cas.Integer(-1)

    @property
    def _beta(self) -> cas.Expr:
        return self.mu_n * self.Cox * (self.W / self.L)

    def _V_TH(self, V_SB_eff: cas.Expr) -> cas.Expr:
        """Threshold with long-channel body-effect correction."""
        return (
            self.V_TH0
            + self.gamma * (cas.sqrt(self.phi + V_SB_eff) - cas.sqrt(self.phi))
        )

    def _V_off(self, V_TH: cas.Expr) -> cas.Expr:
        """Strong/weak split point at the current V_TH."""
        return V_TH + 2 * self.m * self.V_T

    def _I_off(self) -> cas.Expr:
        """Boundary current — independent of V_TH (only of β, m, V_T)."""
        return 2 * self._beta * (self.m * self.V_T) ** 2

    # ------------------------------------------------------------------
    # Numeric helpers.
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

    def _V_TH_num(self, V_SB_eff: float) -> float:
        import math
        V_TH0 = self._to_float(self.V_TH0)
        gamma = self._to_float(self.gamma)
        phi   = self._to_float(self.phi)
        # Clip the sqrt argument at zero so a transient negative V_SB
        # during a Newton iterate doesn't take us into the imaginary
        # axis. Physical operation always has V_SB ≥ 0.
        return V_TH0 + gamma * (math.sqrt(max(0.0, phi + V_SB_eff)) - math.sqrt(phi))

    def operating_region(self, V_GS, V_DS, V_BS=0.0) -> str:
        """Classify the device by region at a given bias point.

        Returns ``"weak_inversion"``, ``"triode"`` or ``"saturation"``.
        ``V_BS`` defaults to 0 — pass the actual bulk-source bias when
        the body is at a different potential.
        """
        pol = 1.0 if self.polarity == "N" else -1.0
        V_GS_eff = pol * self._to_float(V_GS)
        V_DS_eff = pol * self._to_float(V_DS)
        V_SB_eff = -pol * self._to_float(V_BS)
        V_TH = self._V_TH_num(V_SB_eff)
        m   = self._to_float(self.m)
        V_T = self._to_float(self.V_T)
        V_off = V_TH + 2.0 * m * V_T
        if V_GS_eff < V_off:
            return "weak_inversion"
        if V_DS_eff < V_GS_eff - V_TH:
            return "triode"
        return "saturation"

    def dc_current(self, V_GS, V_DS, V_BS=0.0) -> float:
        """Drain current ``I_D`` (SPICE sign — positive *into* the drain)."""
        import math
        pol = 1.0 if self.polarity == "N" else -1.0
        V_GS_eff = pol * self._to_float(V_GS)
        V_DS_eff = pol * self._to_float(V_DS)
        V_SB_eff = -pol * self._to_float(V_BS)
        V_TH = self._V_TH_num(V_SB_eff)
        m    = self._to_float(self.m)
        V_T  = self._to_float(self.V_T)
        beta = (
            self._to_float(self.mu_n) * self._to_float(self.Cox)
            * self._to_float(self.W) / self._to_float(self.L)
        )
        lam = self._to_float(self.lam)
        V_off = V_TH + 2.0 * m * V_T
        I_off_val = 2.0 * beta * (m * V_T) ** 2
        if V_GS_eff < V_off:
            shape = (1.0 - math.exp(-V_DS_eff / V_T)) if V_DS_eff > 0 else 0.0
            mag = (
                I_off_val
                * math.exp((V_GS_eff - V_off) / (m * V_T))
                * shape
                * (1.0 + lam * V_DS_eff)
            )
            return pol * mag
        V_ov = V_GS_eff - V_TH
        if V_DS_eff < V_ov:
            mag = beta * (V_ov * V_DS_eff - 0.5 * V_DS_eff ** 2) * (1.0 + lam * V_DS_eff)
        else:
            mag = 0.5 * beta * V_ov ** 2 * (1.0 + lam * V_DS_eff)
        return pol * mag

    # ------------------------------------------------------------------
    # Symbolic DC current — Piecewise that the MNA / Newton solver use.
    # ------------------------------------------------------------------
    def _I_D_expr(self, V_GS: cas.Expr, V_DS: cas.Expr, V_BS: cas.Expr) -> cas.Expr:
        pol = self._pol
        V_GS_eff = pol * V_GS
        V_DS_eff = pol * V_DS
        V_SB_eff = -pol * V_BS
        V_TH = self._V_TH(V_SB_eff)
        beta = self._beta
        V_off = self._V_off(V_TH)
        I_off = self._I_off()
        V_ov = V_GS_eff - V_TH

        I_sat = cas.Rational(1, 2) * beta * V_ov ** 2 * (1 + self.lam * V_DS_eff)
        I_tri = (
            beta * (V_ov * V_DS_eff - cas.Rational(1, 2) * V_DS_eff ** 2)
            * (1 + self.lam * V_DS_eff)
        )
        # Weak-inversion shape factor — saturates at 1 for V_DS_eff
        # past a few V_T and vanishes at V_DS = 0. Clamped to zero
        # for V_DS_eff < 0 so a Newton iterate that briefly probes
        # negative V_DS doesn't blow up via exp(positive/V_T): the
        # NMOS doesn't conduct in reverse anyway (we don't model the
        # body diode here). The Piecewise is sympy-friendly and keeps
        # the lambdified Jacobian bounded.
        shape = cas.Piecewise(
            (1 - cas.exp(-V_DS_eff / self.V_T), V_DS_eff > 0),
            (cas.Integer(0), True),
        )
        I_weak = (
            I_off
            * cas.exp((V_GS_eff - V_off) / (self.m * self.V_T))
            * shape
            * (1 + self.lam * V_DS_eff)
        )

        I_strong = cas.Piecewise(
            (I_tri, V_DS_eff < V_ov),
            (I_sat, True),
        )
        I_mag = cas.Piecewise(
            (I_weak, V_GS_eff < V_off),
            (I_strong, True),
        )
        return pol * I_mag

    def _small_signal_params(self) -> tuple[cas.Expr, cas.Expr, cas.Expr]:
        _vgs, _vds, _vbs = cas.Dummy("vgs"), cas.Dummy("vds"), cas.Dummy("vbs")
        I_D = self._I_D_expr(_vgs, _vds, _vbs)
        sub = {_vgs: self.V_GS_op, _vds: self.V_DS_op, _vbs: self.V_BS_op}
        g_m  = cas.diff(I_D, _vgs).subs(sub)
        g_ds = cas.diff(I_D, _vds).subs(sub)
        g_mb = cas.diff(I_D, _vbs).subs(sub)
        return g_m, g_ds, g_mb

    # ------------------------------------------------------------------
    # MNA stamps.
    # ------------------------------------------------------------------
    def noise_sources(self) -> list[NoiseSource]:
        out: list[NoiseSource] = []
        if "thermal" in self.include_noise:
            g_m, _, _ = self._small_signal_params()
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
            out.append(
                NoiseSource(
                    name=f"{self.name}.shot",
                    kind="shot",
                    n_plus=self.drain,
                    n_minus=self.source,
                    psd=2 * q * self._I_off(),
                )
            )
        return out

    def stamp(self, ctx: StampContext) -> None:
        if ctx.mode != "ac":
            return
        g_m, g_ds, g_mb = self._small_signal_params()
        s = ctx.s
        d = ctx.n(self.drain)
        g = ctx.n(self.gate)
        src = ctx.n(self.source)
        bulk = ctx.n(self.bulk)

        # VCCS g_m * (V(g) - V(s)).
        if d >= 0:
            if g >= 0:
                ctx.A[d, g] += g_m
            if src >= 0:
                ctx.A[d, src] -= g_m
        if src >= 0:
            if g >= 0:
                ctx.A[src, g] -= g_m
            ctx.A[src, src] += g_m

        # Output conductance g_ds.
        if d >= 0:
            ctx.A[d, d] += g_ds
        if src >= 0:
            ctx.A[src, src] += g_ds
        if d >= 0 and src >= 0:
            ctx.A[d, src] -= g_ds
            ctx.A[src, d] -= g_ds

        # Body transconductance g_mb * (V(b) - V(s)) — drives drain
        # current. Identical stamp shape to g_m but with the bulk
        # node in place of the gate. Ground-tied bulk simply skips
        # the rows that touch a non-existent index.
        if d >= 0:
            if bulk >= 0:
                ctx.A[d, bulk] += g_mb
            if src >= 0:
                ctx.A[d, src] -= g_mb
        if src >= 0:
            if bulk >= 0:
                ctx.A[src, bulk] -= g_mb
            ctx.A[src, src] += g_mb

        # Intrinsic capacitances (admittance s*C). Bulk is treated as
        # its own node, but C_gs / C_gd remain to the source / drain
        # by convention; a 4T model with bulk capacitances would add
        # C_sb / C_db here.
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
        bulk = ctx.n(self.bulk)

        V_d = ctx.x[d]    if d >= 0    else cas.Integer(0)
        V_g = ctx.x[g]    if g >= 0    else cas.Integer(0)
        V_s = ctx.x[src]  if src >= 0  else cas.Integer(0)
        V_b = ctx.x[bulk] if bulk >= 0 else cas.Integer(0)

        I_D = self._I_D_expr(V_g - V_s, V_d - V_s, V_b - V_s)
        if d >= 0:
            ctx.residuals[d] += I_D
        if src >= 0:
            ctx.residuals[src] -= I_D


class NMOS_4T(_MOSFET_4T):
    """Four-terminal segmented L1 + matched-weak-inversion NMOS."""
    polarity: ClassVar[str] = "N"


class PMOS_4T(_MOSFET_4T):
    """Four-terminal segmented L1 + matched-weak-inversion PMOS
    (``V_TH0`` is a positive magnitude)."""
    polarity: ClassVar[str] = "P"


# ---------------------------------------------------------------------------
# Three-terminal wrappers — bulk tied to source so the body effect drops out.
# ---------------------------------------------------------------------------
class NMOS_3T(NMOS_4T):
    """NMOS_4T with the bulk tied to the source.

    Equivalent to ``NMOS_4T(..., bulk=source, V_TH0=V_TH, ...)`` — the
    constructor just supplies ``bulk = source`` for you and accepts
    ``V_TH`` as a friendlier name for the zero-bias threshold. All the
    rest of the model — port set, DC equations, AC stamps, body-effect
    plumbing (with V_SB ≡ 0), noise sources — is inherited verbatim,
    so a 3T instance is genuinely a 4T whose body is shorted out.

    Callers wanting body effect should pass ``gamma > 0`` to
    :class:`NMOS_4T` directly with a distinct bulk node.
    """

    def __init__(
        self,
        name: str,
        drain: str,
        gate: str,
        source: str,
        mu_n,
        Cox,
        W,
        L,
        V_TH,
        **kwargs,
    ) -> None:
        super().__init__(
            name=name,
            drain=drain,
            gate=gate,
            source=source,
            bulk=source,
            mu_n=mu_n,
            Cox=Cox,
            W=W,
            L=L,
            V_TH0=V_TH,
            **kwargs,
        )

    @property
    def V_TH(self) -> cas.Expr:
        """Alias for ``V_TH0`` — kept for backwards compatibility with
        the original (pre-4T) MOSFET_3T API."""
        return self.V_TH0


class PMOS_3T(PMOS_4T):
    """PMOS_4T with the bulk tied to the source. See :class:`NMOS_3T`
    for the rationale; identical wrapper, opposite polarity."""

    def __init__(
        self,
        name: str,
        drain: str,
        gate: str,
        source: str,
        mu_n,
        Cox,
        W,
        L,
        V_TH,
        **kwargs,
    ) -> None:
        super().__init__(
            name=name,
            drain=drain,
            gate=gate,
            source=source,
            bulk=source,
            mu_n=mu_n,
            Cox=Cox,
            W=W,
            L=L,
            V_TH0=V_TH,
            **kwargs,
        )

    @property
    def V_TH(self) -> cas.Expr:
        return self.V_TH0
