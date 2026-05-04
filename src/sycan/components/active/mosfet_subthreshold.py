"""Sub-threshold MOSFET, polarity-aware (NMOS / PMOS).

Drain current in the weak-inversion regime, with
``pol = +1`` (NMOS) or ``-1`` (PMOS)::

    V_GS_eff = pol * (V(gate)  - V(source))
    V_DS_eff = pol * (V(drain) - V(source))
    I_D_mag  = mu_n * Cox * (W/L) * V_T**2
               * exp((V_GS_eff - m * V_TH) / (m * V_T))
               * (1 - exp(-V_DS_eff / V_T))
    I_D_SPICE = pol * I_D_mag     # current INTO drain (SPICE convention)

``m = 1 + C_d/C_ox`` is the sub-threshold slope factor and ``V_T`` is
the thermal voltage; ``V_TH`` is stored as a positive magnitude for
both polarities. Only DC analysis is modelled.

Concrete classes :class:`NMOS_subthreshold` and
:class:`PMOS_subthreshold` fix ``polarity`` via a class variable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Optional

from sycan import cas as cas

from sycan.mna import Component, NoiseSource, NoiseSpec, StampContext, q, freq

# Thermal voltage kT/q at ~300 K, in volts.
_DEFAULT_VT = cas.Rational(2585, 100000)
# Typical sub-threshold slope factor for a long-channel MOSFET.
_DEFAULT_M = cas.Rational(3, 2)


@dataclass
class _MOSFET_subthreshold(Component):
    name: str
    drain: str
    gate: str
    source: str
    mu_n: cas.Expr
    Cox: cas.Expr
    W: cas.Expr
    L: cas.Expr
    V_TH: cas.Expr  # positive magnitude for both polarities
    m: cas.Expr = field(default_factory=lambda: _DEFAULT_M)
    V_T: cas.Expr = field(default_factory=lambda: _DEFAULT_VT)
    I_op: Optional[cas.Expr] = None
    KF: cas.Expr = field(default_factory=lambda: cas.Integer(0))
    AF: cas.Expr = field(default_factory=lambda: cas.Integer(1))
    EF: cas.Expr = field(default_factory=lambda: cas.Integer(1))
    include_noise: NoiseSpec = field(default=None, kw_only=True)

    ports: ClassVar[tuple[str, ...]] = ("drain", "gate", "source")
    has_nonlinear: ClassVar[bool] = True
    polarity: ClassVar[str] = ""  # overridden by concrete subclasses
    SUPPORTED_NOISE: ClassVar[frozenset[str]] = frozenset({"shot", "flicker"})

    def __post_init__(self) -> None:
        if self.polarity not in ("N", "P"):
            raise TypeError(
                "_MOSFET_subthreshold is abstract; instantiate "
                "NMOS_subthreshold or PMOS_subthreshold."
            )
        self.mu_n = cas.sympify(self.mu_n)
        self.Cox = cas.sympify(self.Cox)
        self.W = cas.sympify(self.W)
        self.L = cas.sympify(self.L)
        self.V_TH = cas.sympify(self.V_TH)
        self.m = cas.sympify(self.m)
        self.V_T = cas.sympify(self.V_T)
        self.KF = cas.sympify(self.KF)
        self.AF = cas.sympify(self.AF)
        self.EF = cas.sympify(self.EF)
        if self.I_op is None:
            self.I_op = cas.Symbol(f"I_op_{self.name}")
        else:
            self.I_op = cas.sympify(self.I_op)
        self.include_noise = self._normalize_noise(self.include_noise)

    def noise_sources(self) -> list[NoiseSource]:
        out: list[NoiseSource] = []
        if "shot" in self.include_noise:
            out.append(
                NoiseSource(
                    name=f"{self.name}.shot",
                    kind="shot",
                    n_plus=self.drain,
                    n_minus=self.source,
                    psd=2 * q * self.I_op,
                )
            )
        if "flicker" in self.include_noise and self.KF != 0:
            out.append(
                NoiseSource(
                    name=f"{self.name}.flicker",
                    kind="flicker",
                    n_plus=self.drain,
                    n_minus=self.source,
                    psd=self.KF * self.I_op ** self.AF / (freq ** self.EF),
                )
            )
        return out

    @property
    def _pol(self) -> cas.Expr:
        return cas.Integer(1) if self.polarity == "N" else cas.Integer(-1)

    def stamp(self, ctx: StampContext) -> None:
        return None

    def stamp_nonlinear(self, ctx: StampContext) -> None:
        if ctx.mode != "dc":
            return
        assert ctx.x is not None and ctx.residuals is not None

        d_idx = ctx.n(self.drain)
        g_idx = ctx.n(self.gate)
        s_idx = ctx.n(self.source)

        V_d = ctx.x[d_idx] if d_idx >= 0 else cas.Integer(0)
        V_g = ctx.x[g_idx] if g_idx >= 0 else cas.Integer(0)
        V_s = ctx.x[s_idx] if s_idx >= 0 else cas.Integer(0)

        pol = self._pol
        V_GS_eff = pol * (V_g - V_s)
        V_DS_eff = pol * (V_d - V_s)

        prefactor = self.mu_n * self.Cox * (self.W / self.L) * self.V_T ** 2
        I_D_mag = (
            prefactor
            * cas.exp((V_GS_eff - self.m * self.V_TH) / (self.m * self.V_T))
            * (1 - cas.exp(-V_DS_eff / self.V_T))
        )
        I_D = pol * I_D_mag

        if d_idx >= 0:
            ctx.residuals[d_idx] += I_D
        if s_idx >= 0:
            ctx.residuals[s_idx] -= I_D


class NMOS_subthreshold(_MOSFET_subthreshold):
    """Weak-inversion NMOS."""
    polarity: ClassVar[str] = "N"


class PMOS_subthreshold(_MOSFET_subthreshold):
    """Weak-inversion PMOS."""
    polarity: ClassVar[str] = "P"
