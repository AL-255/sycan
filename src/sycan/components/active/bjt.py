"""SPICE Gummel-Poon BJT — DC and hybrid-pi AC model (SPICE ``Q``).

The DC equations follow Gray & Meyer §1.4 and the SPICE G-P manual.
All standard DC effects are included:

* ideal forward/reverse transport currents (``IS``, ``NF``, ``NR``),
* ideal forward/reverse current gains (``BF``, ``BR``),
* non-ideal base-emitter / base-collector leakage diodes
  (``ISE``, ``NE`` and ``ISC``, ``NC``),
* base-width modulation via forward/reverse Early voltages
  (``VAF``, ``VAR``),
* high-level-injection roll-off via forward/reverse knee currents
  (``IKF``, ``IKR``) through the base-charge factor.

The AC model is a simplified hybrid-pi with:

* ``g_m  = I_C_op / (NF·V_T)``  — transconductance,
* ``r_pi = BF / g_m``           — base-emitter resistance,
* ``r_o  = VAF / I_C_op``       — output resistance (omitted if ``VAF = oo``),
* ``C_pi``, ``C_mu``            — junction/diffusion capacitances.

Default parameters model an ideal Ebers-Moll transistor:
``VAF = VAR = IKF = IKR = oo`` (no Early, no knee), ``ISE = ISC = 0``
(no leakage), ``NF = NR = 1``, ``NE = 1.5``, ``NC = 2``,
``V_T = 25.85 mV``. Only ``IS``, ``BF``, ``BR`` must be supplied.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Optional

from sycan import cas as cas

from sycan.mna import Component, NoiseSource, NoiseSpec, StampContext, q, freq

_DEFAULT_VT = cas.Rational(2585, 100000)


@dataclass
class BJT(Component):
    """Gummel-Poon BJT with hybrid-pi AC model.

    With ``include_noise="shot"`` (or ``"all"``) two shot-noise current
    sources are attached: one between collector and emitter with PSD
    ``2·q·I_C_op`` (collector current shot), one between base and
    emitter with PSD ``2·q·I_B_op`` (base current shot). Operating-point
    currents default to per-instance symbols ``I_C_op_<name>`` and
    ``I_B_op_<name>``; pass values to override.

    ``C_pi`` (base-emitter) and ``C_mu`` (base-collector) capacitances
    default to 0; set them for frequency-dependent AC behaviour.
    """

    name: str
    collector: str
    base: str
    emitter: str
    polarity: str  # "NPN" or "PNP"
    IS: cas.Expr
    BF: cas.Expr
    BR: cas.Expr
    NF: cas.Expr = field(default_factory=lambda: cas.Integer(1))
    NR: cas.Expr = field(default_factory=lambda: cas.Integer(1))
    VAF: cas.Expr = field(default_factory=lambda: cas.oo)
    VAR: cas.Expr = field(default_factory=lambda: cas.oo)
    IKF: cas.Expr = field(default_factory=lambda: cas.oo)
    IKR: cas.Expr = field(default_factory=lambda: cas.oo)
    ISE: cas.Expr = field(default_factory=lambda: cas.Integer(0))
    NE: cas.Expr = field(default_factory=lambda: cas.Rational(3, 2))
    ISC: cas.Expr = field(default_factory=lambda: cas.Integer(0))
    NC: cas.Expr = field(default_factory=lambda: cas.Integer(2))
    V_T: cas.Expr = field(default_factory=lambda: _DEFAULT_VT)
    I_C_op: Optional[cas.Expr] = None
    I_B_op: Optional[cas.Expr] = None
    C_pi: cas.Expr = field(default_factory=lambda: cas.Integer(0))
    C_mu: cas.Expr = field(default_factory=lambda: cas.Integer(0))
    KF: cas.Expr = field(default_factory=lambda: cas.Integer(0))
    AF: cas.Expr = field(default_factory=lambda: cas.Integer(1))
    include_noise: NoiseSpec = field(default=None, kw_only=True)

    ports: ClassVar[tuple[str, ...]] = ("collector", "base", "emitter")
    has_nonlinear: ClassVar[bool] = True
    SUPPORTED_NOISE: ClassVar[frozenset[str]] = frozenset({"shot", "flicker"})

    def __post_init__(self) -> None:
        self.polarity = self.polarity.upper()
        if self.polarity not in ("NPN", "PNP"):
            raise ValueError(
                f"BJT {self.name!r}: polarity must be 'NPN' or 'PNP', got "
                f"{self.polarity!r}"
            )
        for attr in (
            "IS", "BF", "BR", "NF", "NR",
            "VAF", "VAR", "IKF", "IKR",
            "ISE", "NE", "ISC", "NC", "V_T",
        ):
            setattr(self, attr, cas.sympify(getattr(self, attr)))
        self.C_pi = cas.sympify(self.C_pi)
        self.C_mu = cas.sympify(self.C_mu)
        self.KF = cas.sympify(self.KF)
        self.AF = cas.sympify(self.AF)
        if self.I_C_op is None:
            self.I_C_op = cas.Symbol(f"I_C_op_{self.name}")
        else:
            self.I_C_op = cas.sympify(self.I_C_op)
        if self.I_B_op is None:
            self.I_B_op = cas.Symbol(f"I_B_op_{self.name}")
        else:
            self.I_B_op = cas.sympify(self.I_B_op)
        self.include_noise = self._normalize_noise(self.include_noise)

    def _hybrid_pi_params(self) -> dict[str, cas.Expr]:
        """Return g_m, r_pi, r_o for the hybrid-pi AC model."""
        g_m = self.I_C_op / (self.NF * self.V_T)
        r_pi = self.BF / g_m
        r_o = cas.oo if self.VAF == cas.oo else self.VAF / self.I_C_op
        return {"g_m": g_m, "r_pi": r_pi, "r_o": r_o}

    def noise_sources(self) -> list[NoiseSource]:
        out: list[NoiseSource] = []
        if "shot" in self.include_noise:
            out.append(
                NoiseSource(
                    name=f"{self.name}.shot.collector",
                    kind="shot",
                    n_plus=self.collector,
                    n_minus=self.emitter,
                    psd=2 * q * self.I_C_op,
                )
            )
            out.append(
                NoiseSource(
                    name=f"{self.name}.shot.base",
                    kind="shot",
                    n_plus=self.base,
                    n_minus=self.emitter,
                    psd=2 * q * self.I_B_op,
                )
            )
        if "flicker" in self.include_noise and self.KF != 0:
            out.append(
                NoiseSource(
                    name=f"{self.name}.flicker",
                    kind="flicker",
                    n_plus=self.collector,
                    n_minus=self.emitter,
                    psd=self.KF * self.I_C_op ** self.AF / freq,
                )
            )
        return out

    def _stamp_conductance(self, ctx: StampContext, g: cas.Expr,
                           p_a: int, p_b: int) -> None:
        """Stamp a conductance g between nodes p_a and p_b."""
        if p_a >= 0:
            ctx.A[p_a, p_a] += g
        if p_b >= 0:
            ctx.A[p_b, p_b] += g
        if p_a >= 0 and p_b >= 0:
            ctx.A[p_a, p_b] -= g
            ctx.A[p_b, p_a] -= g

    def _stamp_vccs(self, ctx: StampContext, g_m: cas.Expr,
                    ctrl_p: int, ctrl_n: int,
                    out_p: int, out_n: int) -> None:
        """Stamp a VCCS g_m*(V(ctrl_p)-V(ctrl_n)) into out_p→out_n."""
        for p, sign in ((out_p, +1), (out_n, -1)):
            if p < 0:
                continue
            if ctrl_p >= 0:
                ctx.A[p, ctrl_p] += sign * g_m
            if ctrl_n >= 0:
                ctx.A[p, ctrl_n] -= sign * g_m

    def _stamp_cap(self, ctx: StampContext, c: cas.Expr,
                   p_a: int, p_b: int) -> None:
        """Stamp an admittance s·C between nodes p_a and p_b."""
        if c == 0:
            return
        Y = ctx.s * c
        self._stamp_conductance(ctx, Y, p_a, p_b)

    def stamp(self, ctx: StampContext) -> None:
        if ctx.mode != "ac":
            return
        hp = self._hybrid_pi_params()
        c, b, e = ctx.n(self.collector), ctx.n(self.base), ctx.n(self.emitter)

        # g_m VCCS: collector → emitter, controlled by V(base)-V(emitter).
        self._stamp_vccs(ctx, hp["g_m"], b, e, c, e)

        # r_pi conductance: base → emitter.
        self._stamp_conductance(ctx, 1 / hp["r_pi"], b, e)

        # r_o conductance: collector → emitter (if finite).
        if hp["r_o"] != cas.oo:
            self._stamp_conductance(ctx, 1 / hp["r_o"], c, e)

        # Intrinsic capacitances.
        self._stamp_cap(ctx, self.C_pi, b, e)
        self._stamp_cap(ctx, self.C_mu, b, c)

    def stamp_nonlinear(self, ctx: StampContext) -> None:
        if ctx.mode != "dc":
            return
        assert ctx.x is not None and ctx.residuals is not None

        c_idx = ctx.n(self.collector)
        b_idx = ctx.n(self.base)
        e_idx = ctx.n(self.emitter)

        V_C = ctx.x[c_idx] if c_idx >= 0 else cas.Integer(0)
        V_B = ctx.x[b_idx] if b_idx >= 0 else cas.Integer(0)
        V_E = ctx.x[e_idx] if e_idx >= 0 else cas.Integer(0)

        pol = cas.Integer(1) if self.polarity == "NPN" else cas.Integer(-1)
        V_BE = pol * (V_B - V_E)
        V_BC = pol * (V_B - V_C)

        # Ideal transport currents.
        I_BF = self.IS * (cas.exp(V_BE / (self.NF * self.V_T)) - 1)
        I_BR = self.IS * (cas.exp(V_BC / (self.NR * self.V_T)) - 1)

        # Base-charge factor (Early + high-level injection).
        q_1 = 1 / (1 - V_BC / self.VAF - V_BE / self.VAR)
        q_2 = I_BF / self.IKF + I_BR / self.IKR
        q_B = (q_1 / 2) * (1 + cas.sqrt(1 + 4 * q_2))

        # Non-ideal leakage diodes (skip cleanly when ISE/ISC are zero).
        I_BE_leak = self.ISE * (cas.exp(V_BE / (self.NE * self.V_T)) - 1)
        I_BC_leak = self.ISC * (cas.exp(V_BC / (self.NC * self.V_T)) - 1)

        # Internal branch currents.
        I_BE_total = I_BF / self.BF + I_BE_leak
        I_BC_total = I_BR / self.BR + I_BC_leak
        I_CE = (I_BF - I_BR) / q_B

        # Terminal currents INTO each terminal.
        I_C = pol * (I_CE - I_BC_total)
        I_B = pol * (I_BE_total + I_BC_total)
        I_E = -(I_C + I_B)

        # KCL residual: these currents leave each node into the BJT.
        if c_idx >= 0:
            ctx.residuals[c_idx] += I_C
        if b_idx >= 0:
            ctx.residuals[b_idx] += I_B
        if e_idx >= 0:
            ctx.residuals[e_idx] += I_E
