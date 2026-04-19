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
from typing import ClassVar

import sympy as sp

from sycan.mna import Component, StampContext

# Thermal voltage kT/q at ~300 K, in volts.
_DEFAULT_VT = sp.Rational(2585, 100000)
# Typical sub-threshold slope factor for a long-channel MOSFET.
_DEFAULT_M = sp.Rational(3, 2)


@dataclass
class _MOSFET_subthreshold(Component):
    name: str
    drain: str
    gate: str
    source: str
    mu_n: sp.Expr
    Cox: sp.Expr
    W: sp.Expr
    L: sp.Expr
    V_TH: sp.Expr  # positive magnitude for both polarities
    m: sp.Expr = field(default_factory=lambda: _DEFAULT_M)
    V_T: sp.Expr = field(default_factory=lambda: _DEFAULT_VT)

    has_nonlinear: ClassVar[bool] = True
    polarity: ClassVar[str] = ""  # overridden by concrete subclasses

    def __post_init__(self) -> None:
        if self.polarity not in ("N", "P"):
            raise TypeError(
                "_MOSFET_subthreshold is abstract; instantiate "
                "NMOS_subthreshold or PMOS_subthreshold."
            )
        self.mu_n = sp.sympify(self.mu_n)
        self.Cox = sp.sympify(self.Cox)
        self.W = sp.sympify(self.W)
        self.L = sp.sympify(self.L)
        self.V_TH = sp.sympify(self.V_TH)
        self.m = sp.sympify(self.m)
        self.V_T = sp.sympify(self.V_T)

    @property
    def _pol(self) -> sp.Expr:
        return sp.Integer(1) if self.polarity == "N" else sp.Integer(-1)

    def stamp(self, ctx: StampContext) -> None:
        return None

    def stamp_nonlinear(self, ctx: StampContext) -> None:
        if ctx.mode != "dc":
            return
        assert ctx.x is not None and ctx.residuals is not None

        d_idx = ctx.n(self.drain)
        g_idx = ctx.n(self.gate)
        s_idx = ctx.n(self.source)

        V_d = ctx.x[d_idx] if d_idx >= 0 else sp.Integer(0)
        V_g = ctx.x[g_idx] if g_idx >= 0 else sp.Integer(0)
        V_s = ctx.x[s_idx] if s_idx >= 0 else sp.Integer(0)

        pol = self._pol
        V_GS_eff = pol * (V_g - V_s)
        V_DS_eff = pol * (V_d - V_s)

        prefactor = self.mu_n * self.Cox * (self.W / self.L) * self.V_T ** 2
        I_D_mag = (
            prefactor
            * sp.exp((V_GS_eff - self.m * self.V_TH) / (self.m * self.V_T))
            * (1 - sp.exp(-V_DS_eff / self.V_T))
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
