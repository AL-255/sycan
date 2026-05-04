"""Shockley diode — DC and small-signal AC model (SPICE ``D``).

DC::

    I_D = IS * (exp(V_D / (N V_T)) - 1)

with ``V_D = V(anode) - V(cathode)``.

AC small-signal linearisation produces a conductance ``g_d = dI_D/dV_D``
evaluated at ``V_D_op``, plus an optional junction capacitance ``C_j``
stamped as admittance ``s·C_j``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Optional

from sycan import cas as cas

from sycan.mna import Component, NoiseSource, NoiseSpec, StampContext, q

_DEFAULT_VT = cas.Rational(2585, 100000)


@dataclass
class Diode(Component):
    """Shockley diode with small-signal AC model.

    Pass ``include_noise="shot"`` (or ``"all"``) to attach a shot-noise
    current source between anode and cathode with one-sided PSD
    ``2·q·I_op``. ``I_op`` defaults to a per-instance symbolic
    operating-point current named ``I_op_<diode-name>``; supply a
    sympy expression to pin it down.

    ``C_j`` (junction capacitance, default 0) is stamped as ``s·C_j``
    in AC analysis. ``V_D_op`` is the DC operating-point voltage used
    to compute the small-signal conductance ``g_d``; it defaults to a
    per-instance symbol ``V_D_op_<name>``.
    """

    name: str
    anode: str
    cathode: str
    IS: cas.Expr
    N: cas.Expr = field(default_factory=lambda: cas.Integer(1))
    V_T: cas.Expr = field(default_factory=lambda: _DEFAULT_VT)
    I_op: Optional[cas.Expr] = None
    C_j: cas.Expr = field(default_factory=lambda: cas.Integer(0))
    V_D_op: Optional[cas.Expr] = None
    include_noise: NoiseSpec = field(default=None, kw_only=True)

    ports: ClassVar[tuple[str, ...]] = ("anode", "cathode")
    has_nonlinear: ClassVar[bool] = True
    SUPPORTED_NOISE: ClassVar[frozenset[str]] = frozenset({"shot"})

    def __post_init__(self) -> None:
        self.IS = cas.sympify(self.IS)
        self.N = cas.sympify(self.N)
        self.V_T = cas.sympify(self.V_T)
        self.C_j = cas.sympify(self.C_j)
        if self.I_op is None:
            self.I_op = cas.Symbol(f"I_op_{self.name}")
        else:
            self.I_op = cas.sympify(self.I_op)
        if self.V_D_op is None:
            self.V_D_op = cas.Symbol(f"V_D_op_{self.name}")
        else:
            self.V_D_op = cas.sympify(self.V_D_op)
        self.include_noise = self._normalize_noise(self.include_noise)

    def _small_signal_gd(self) -> cas.Expr:
        """Small-signal conductance g_d = dI_D/dV_D at the operating point."""
        _vd = cas.Dummy("vd")
        I_D = self.IS * (cas.exp(_vd / (self.N * self.V_T)) - 1)
        return cas.diff(I_D, _vd).subs({_vd: self.V_D_op})

    def noise_sources(self) -> list[NoiseSource]:
        out: list[NoiseSource] = []
        if "shot" in self.include_noise:
            out.append(
                NoiseSource(
                    name=f"{self.name}.shot",
                    kind="shot",
                    n_plus=self.anode,
                    n_minus=self.cathode,
                    psd=2 * q * self.I_op,
                )
            )
        return out

    def stamp(self, ctx: StampContext) -> None:
        if ctx.mode != "ac":
            return
        g_d = self._small_signal_gd()
        a, k = ctx.n(self.anode), ctx.n(self.cathode)

        # Small-signal conductance between anode and cathode.
        if a >= 0:
            ctx.A[a, a] += g_d
        if k >= 0:
            ctx.A[k, k] += g_d
        if a >= 0 and k >= 0:
            ctx.A[a, k] -= g_d
            ctx.A[k, a] -= g_d

        # Junction capacitance admittance s·C_j.
        if self.C_j != 0:
            Y = ctx.s * self.C_j
            if a >= 0:
                ctx.A[a, a] += Y
            if k >= 0:
                ctx.A[k, k] += Y
            if a >= 0 and k >= 0:
                ctx.A[a, k] -= Y
                ctx.A[k, a] -= Y

    def stamp_nonlinear(self, ctx: StampContext) -> None:
        if ctx.mode != "dc":
            return
        assert ctx.x is not None and ctx.residuals is not None

        a_idx = ctx.n(self.anode)
        k_idx = ctx.n(self.cathode)

        V_a = ctx.x[a_idx] if a_idx >= 0 else cas.Integer(0)
        V_k = ctx.x[k_idx] if k_idx >= 0 else cas.Integer(0)
        V_D = V_a - V_k

        I_D = self.IS * (cas.exp(V_D / (self.N * self.V_T)) - 1)

        # I_D flows anode → cathode through the diode; externally it
        # leaves the anode node and enters the cathode node.
        if a_idx >= 0:
            ctx.residuals[a_idx] += I_D
        if k_idx >= 0:
            ctx.residuals[k_idx] -= I_D
