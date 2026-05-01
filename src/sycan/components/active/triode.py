"""Vacuum-tube triode with Langmuir 3/2-power DC law and small-signal
AC model derived by differentiation.

**DC**::

    I_p = K * (mu * V_gk + V_pk) ** (3/2)

(valid in the forward-conduction region ``mu*V_gk + V_pk > 0``), with

* ``V_gk = V(grid)  - V(cathode)``
* ``V_pk = V(plate) - V(cathode)``

Plate current flows from plate to cathode internally, so externally it
enters the plate terminal and exits the cathode — the same sign
convention as the drain-current of an NMOS.

**AC small-signal** — obtained by differentiating ``I_p`` at the
operating point ``(V_g_op, V_p_op)``::

    g_m = dI_p/dV_gk |_OP = (3/2) K mu (mu V_g_op + V_p_op) ** (1/2)
    g_p = dI_p/dV_pk |_OP = (3/2) K    (mu V_g_op + V_p_op) ** (1/2)

which obeys the classic triode identity ``mu = g_m / g_p = g_m * r_p``.

Three intrinsic capacitances (grid-cathode, grid-plate, plate-cathode)
can be supplied; the grid-plate cap is the dominant Miller contribution
in a grounded-cathode amplifier.

If ``V_g_op`` / ``V_p_op`` are not provided, per-instance symbols are
generated so that multiple tubes in one circuit do not collide.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Optional

from sycan import cas as cas

from sycan.mna import Component, NoiseSource, NoiseSpec, StampContext, T, k_B


@dataclass
class Triode(Component):
    """Vacuum-tube triode.

    With ``include_noise="thermal"`` (or ``"all"``) a thermal noise
    current source is attached between plate and cathode with PSD
    ``4·k_B·T·g_m`` where ``g_m`` is the small-signal transconductance
    evaluated at ``(V_g_op, V_p_op)``.
    """

    name: str
    plate: str
    grid: str
    cathode: str
    K: cas.Expr    # perveance
    mu: cas.Expr   # amplification factor
    V_g_op: Optional[cas.Expr] = None
    V_p_op: Optional[cas.Expr] = None
    C_gk: cas.Expr = field(default_factory=lambda: cas.Integer(0))
    C_gp: cas.Expr = field(default_factory=lambda: cas.Integer(0))
    C_pk: cas.Expr = field(default_factory=lambda: cas.Integer(0))
    include_noise: NoiseSpec = field(default=None, kw_only=True)

    ports: ClassVar[tuple[str, ...]] = ("plate", "grid", "cathode")
    has_nonlinear: ClassVar[bool] = True
    SUPPORTED_NOISE: ClassVar[frozenset[str]] = frozenset({"thermal"})

    def __post_init__(self) -> None:
        if self.V_g_op is None:
            self.V_g_op = cas.Symbol(f"V_g_op_{self.name}")
        if self.V_p_op is None:
            self.V_p_op = cas.Symbol(f"V_p_op_{self.name}")
        for attr in ("K", "mu", "V_g_op", "V_p_op", "C_gk", "C_gp", "C_pk"):
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
                    n_plus=self.plate,
                    n_minus=self.cathode,
                    psd=4 * k_B * T * g_m,
                )
            )
        return out

    # ------------------------------------------------------------------

    def _I_p_expr(self, V_gk: cas.Expr, V_pk: cas.Expr) -> cas.Expr:
        """Langmuir 3/2-power plate current, symbolic in (V_gk, V_pk)."""
        V_eff = self.mu * V_gk + V_pk
        return self.K * V_eff ** cas.Rational(3, 2)

    def _small_signal_params(self) -> tuple[cas.Expr, cas.Expr]:
        """Return (g_m, g_p) evaluated at the stored operating point."""
        _vgk, _vpk = cas.Dummy("vgk"), cas.Dummy("vpk")
        I_p = self._I_p_expr(_vgk, _vpk)
        sub = {_vgk: self.V_g_op, _vpk: self.V_p_op}
        g_m = cas.diff(I_p, _vgk).subs(sub)
        g_p = cas.diff(I_p, _vpk).subs(sub)
        return g_m, g_p

    # ------------------------------------------------------------------

    def stamp(self, ctx: StampContext) -> None:
        if ctx.mode != "ac":
            return

        g_m, g_p = self._small_signal_params()
        s = ctx.s
        p = ctx.n(self.plate)
        g = ctx.n(self.grid)
        k = ctx.n(self.cathode)

        # VCCS: g_m * (V(g) - V(k)) from plate -> cathode internally.
        if p >= 0:
            if g >= 0:
                ctx.A[p, g] += g_m
            if k >= 0:
                ctx.A[p, k] -= g_m
        if k >= 0:
            if g >= 0:
                ctx.A[k, g] -= g_m
            ctx.A[k, k] += g_m

        # Plate conductance g_p between plate and cathode.
        if p >= 0:
            ctx.A[p, p] += g_p
        if k >= 0:
            ctx.A[k, k] += g_p
        if p >= 0 and k >= 0:
            ctx.A[p, k] -= g_p
            ctx.A[k, p] -= g_p

        # Intrinsic capacitances.
        for c_val, a, b in (
            (self.C_gk, g, k),
            (self.C_gp, g, p),
            (self.C_pk, p, k),
        ):
            Y = s * c_val
            if a >= 0:
                ctx.A[a, a] += Y
            if b >= 0:
                ctx.A[b, b] += Y
            if a >= 0 and b >= 0:
                ctx.A[a, b] -= Y
                ctx.A[b, a] -= Y

    def stamp_nonlinear(self, ctx: StampContext) -> None:
        if ctx.mode != "dc":
            return
        assert ctx.x is not None and ctx.residuals is not None

        p = ctx.n(self.plate)
        g = ctx.n(self.grid)
        k = ctx.n(self.cathode)

        V_p = ctx.x[p] if p >= 0 else cas.Integer(0)
        V_g = ctx.x[g] if g >= 0 else cas.Integer(0)
        V_k = ctx.x[k] if k >= 0 else cas.Integer(0)

        I_p = self._I_p_expr(V_g - V_k, V_p - V_k)
        if p >= 0:
            ctx.residuals[p] += I_p
        if k >= 0:
            ctx.residuals[k] -= I_p
