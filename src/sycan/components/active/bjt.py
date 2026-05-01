"""SPICE Gummel-Poon DC model for a bipolar transistor (NPN or PNP).

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

Let ``pol = +1`` (NPN) or ``-1`` (PNP). Define the internal junction
voltages

    V_BE = pol * (V(base) - V(emitter))
    V_BC = pol * (V(base) - V(collector))

and the ideal transport currents

    I_BF = IS * (exp(V_BE / (NF V_T)) - 1)
    I_BR = IS * (exp(V_BC / (NR V_T)) - 1)

Base-charge factor (normalized majority base charge)::

    q_1 = 1 / (1 - V_BC/VAF - V_BE/VAR)
    q_2 = I_BF/IKF + I_BR/IKR
    q_B = (q_1 / 2) * (1 + sqrt(1 + 4 q_2))

Internal branch currents::

    I_BE = I_BF/BF + ISE * (exp(V_BE/(NE V_T)) - 1)
    I_BC = I_BR/BR + ISC * (exp(V_BC/(NC V_T)) - 1)
    I_CE = (I_BF - I_BR) / q_B

Terminal currents (positive when flowing *into* each terminal),
multiplied by ``pol`` so the PNP has the expected sign-reversal::

    I_C = pol * (I_CE - I_BC)
    I_B = pol * (I_BE + I_BC)
    I_E = -(I_C + I_B)

Default parameters model an ideal Ebers-Moll transistor:
``VAF = VAR = IKF = IKR = oo`` (no Early, no knee), ``ISE = ISC = 0``
(no leakage), ``NF = NR = 1``, ``NE = 1.5``, ``NC = 2``,
``V_T = 25.85 mV``. Only ``IS``, ``BF``, ``BR`` must be supplied.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Optional

from sycan import cas as cas

from sycan.mna import Component, NoiseSource, NoiseSpec, StampContext, q

_DEFAULT_VT = cas.Rational(2585, 100000)


@dataclass
class BJT(Component):
    """Gummel-Poon DC BJT.

    With ``include_noise="shot"`` (or ``"all"``) two shot-noise current
    sources are attached: one between collector and emitter with PSD
    ``2·q·I_C_op`` (collector current shot), one between base and
    emitter with PSD ``2·q·I_B_op`` (base current shot). Operating-point
    currents default to per-instance symbols ``I_C_op_<name>`` and
    ``I_B_op_<name>``; pass values to override.
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
    include_noise: NoiseSpec = field(default=None, kw_only=True)

    ports: ClassVar[tuple[str, ...]] = ("collector", "base", "emitter")
    has_nonlinear: ClassVar[bool] = True
    SUPPORTED_NOISE: ClassVar[frozenset[str]] = frozenset({"shot"})

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
        if self.I_C_op is None:
            self.I_C_op = cas.Symbol(f"I_C_op_{self.name}")
        else:
            self.I_C_op = cas.sympify(self.I_C_op)
        if self.I_B_op is None:
            self.I_B_op = cas.Symbol(f"I_B_op_{self.name}")
        else:
            self.I_B_op = cas.sympify(self.I_B_op)
        self.include_noise = self._normalize_noise(self.include_noise)

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
        return out

    def stamp(self, ctx: StampContext) -> None:
        return None

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
