"""Independent voltage source (SPICE ``V``).

Supports DC, AC-phasor, and waveform modes: ``"sine"``, ``"pulse"``,
``"exp"``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Optional

from sycan import cas as cas

from sycan.mna import Component, NoiseSpec, StampContext


def _sine_laplace(
    s: cas.Expr, amplitude: cas.Expr, omega: cas.Expr, phase: cas.Expr
) -> cas.Expr:
    """Laplace transform of ``A * sin(omega * t + phi)``."""
    return amplitude * (s * cas.sin(phase) + omega * cas.cos(phase)) / (
        s**2 + omega**2
    )


def _pulse_laplace(
    s: cas.Expr, v1: cas.Expr, v2: cas.Expr,
    td: cas.Expr, pw: cas.Expr,
) -> cas.Expr:
    """Laplace transform of a single rectangular pulse.

    V(t) = v1 for t < td, v2 for td ≤ t < td+pw, v1 thereafter.

    ``v1/s + (v2-v1)*(exp(-s*td) - exp(-s*(td+pw))) / s``
    """
    if s == 0:
        return v1 + (v2 - v1) * pw
    dV = v2 - v1
    if pw == cas.oo:
        return v1 / s + dV * cas.exp(-s * td) / s
    return v1 / s + dV * (cas.exp(-s * td) - cas.exp(-s * (td + pw))) / s


def _exp_laplace(
    s: cas.Expr, v1: cas.Expr, v2: cas.Expr,
    td1: cas.Expr, tau1: cas.Expr,
    td2: Optional[cas.Expr] = None, tau2: Optional[cas.Expr] = None,
) -> cas.Expr:
    """Laplace transform of an exponential pulse.

    V(t) = v1 for t < td1,
    rises/falls toward v2 with time constant tau1 for t ≥ td1,
    optionally recovers toward v1 with tau2 from td2.

    Single exponential: ``v1/s + (v2-v1)*exp(-s*td1)/(s*(1+s*tau1))``
    """
    dV = v2 - v1
    rise = dV * cas.exp(-s * td1) / (s * (1 + s * tau1))
    if td2 is not None and tau2 is not None:
        fall = dV * cas.exp(-s * td2) / (s * (1 + s * tau2))
        return v1 / s + rise - fall
    return v1 / s + rise


def waveform_laplace(source, s: cas.Expr) -> cas.Expr:
    """Laplace transform of an independent source's transient waveform.

    ``source`` is a :class:`VoltageSource` or
    :class:`~sycan.components.basic.current_source.CurrentSource`.
    A source without a ``waveform`` is treated as its DC ``value``
    switched on at ``t = 0`` (a step), i.e. ``value / s``.
    """
    wf = source.waveform
    if wf == "sine":
        omega = 2 * cas.pi * source.frequency
        return _sine_laplace(s, source.amplitude, omega, source.phase)
    if wf == "pulse":
        return _pulse_laplace(s, source.v1, source.v2, source.td, source.pw)
    if wf == "exp":
        return _exp_laplace(
            s, source.v1, source.v2,
            source.td1, source.tau1, source.td2, source.tau2,
        )
    return source.value / s


def waveform_time(source, t: cas.Expr) -> cas.Expr:
    """Time-domain expression of an independent source's waveform.

    Delayed pulse / exponential segments are expressed with
    ``Heaviside``. A source without a ``waveform`` returns its DC
    ``value`` — valid for ``t > 0``, matching the step convention
    :func:`waveform_laplace` uses for plain DC sources.
    """
    wf = source.waveform
    if wf == "sine":
        omega = 2 * cas.pi * source.frequency
        return source.amplitude * cas.sin(omega * t + source.phase)
    if wf == "pulse":
        dV = source.v2 - source.v1
        out = source.v1 + dV * cas.Heaviside(t - source.td)
        if source.pw != cas.oo:
            out -= dV * cas.Heaviside(t - source.td - source.pw)
        return out
    if wf == "exp":
        dV = source.v2 - source.v1
        out = source.v1 + dV * (
            1 - cas.exp(-(t - source.td1) / source.tau1)
        ) * cas.Heaviside(t - source.td1)
        if source.td2 is not None and source.tau2 is not None:
            out -= dV * (
                1 - cas.exp(-(t - source.td2) / source.tau2)
            ) * cas.Heaviside(t - source.td2)
        return out
    return source.value


@dataclass
class VoltageSource(Component):
    """Ideal voltage source enforcing ``V(n_plus) - V(n_minus) = value``.

    Its auxiliary current ``I(name)`` is defined from ``n_plus`` to
    ``n_minus`` through the source, matching SPICE's convention.

    ``ac_value`` is the small-signal phasor used in AC analysis. If
    ``None``, AC analysis reuses the DC ``value``. Ideal sources are
    noiseless; ``include_noise`` is accepted for interface uniformity.

    **Waveforms** — set ``waveform`` to model time-varying sources:

    * ``"sine"`` — ``amplitude * sin(2π·frequency·t + phase)``.
    * ``"pulse"`` — rectangular pulse with ``v1``, ``v2``, ``td``
      (delay), ``pw`` (pulse width). DC is ``v1``.
    * ``"exp"`` — exponential pulse with ``v1``, ``v2``, ``td1``
      (rise delay), ``tau1`` (rise time constant), and optional
      ``td2`` / ``tau2`` (fall). DC is ``v1``.
    """

    name: str
    n_plus: str
    n_minus: str
    value: cas.Expr
    ac_value: Optional[cas.Expr] = None
    include_noise: NoiseSpec = field(default=None, kw_only=True)
    waveform: Optional[str] = field(default=None, kw_only=True)
    # sine
    amplitude: Optional[cas.Expr] = field(default=None, kw_only=True)
    frequency: Optional[cas.Expr] = field(default=None, kw_only=True)
    phase: Optional[cas.Expr] = field(default=0, kw_only=True)
    # pulse
    v1: Optional[cas.Expr] = field(default=None, kw_only=True)
    v2: Optional[cas.Expr] = field(default=None, kw_only=True)
    td: Optional[cas.Expr] = field(default=None, kw_only=True)
    pw: Optional[cas.Expr] = field(default=None, kw_only=True)
    # exp
    td1: Optional[cas.Expr] = field(default=None, kw_only=True)
    tau1: Optional[cas.Expr] = field(default=None, kw_only=True)
    td2: Optional[cas.Expr] = field(default=None, kw_only=True)
    tau2: Optional[cas.Expr] = field(default=None, kw_only=True)

    ports: ClassVar[tuple[str, ...]] = ("n_plus", "n_minus")
    has_aux: ClassVar[bool] = True
    SUPPORTED_NOISE: ClassVar[frozenset[str]] = frozenset()

    def __post_init__(self) -> None:
        self.value = cas.sympify(self.value)
        if self.ac_value is not None:
            self.ac_value = cas.sympify(self.ac_value)
        self.include_noise = self._normalize_noise(self.include_noise)
        if self.waveform is not None:
            self._init_waveform()

    def _init_waveform(self) -> None:
        if self.waveform == "sine":
            if self.amplitude is None or self.frequency is None:
                raise ValueError(
                    f"{type(self).__name__}('{self.name}'): "
                    "sine waveform requires amplitude and frequency"
                )
            self.amplitude = cas.sympify(self.amplitude)
            self.frequency = cas.sympify(self.frequency)
            self.phase = cas.sympify(self.phase)
        elif self.waveform == "pulse":
            if self.v1 is None or self.v2 is None:
                raise ValueError(
                    f"{type(self).__name__}('{self.name}'): "
                    "pulse waveform requires v1 and v2"
                )
            self.v1 = cas.sympify(self.v1)
            self.v2 = cas.sympify(self.v2)
            self.td = cas.sympify(self.td) if self.td is not None else 0
            self.pw = cas.sympify(self.pw) if self.pw is not None else cas.oo
        elif self.waveform == "exp":
            if self.v1 is None or self.v2 is None:
                raise ValueError(
                    f"{type(self).__name__}('{self.name}'): "
                    "exp waveform requires v1 and v2"
                )
            if self.td1 is None or self.tau1 is None:
                raise ValueError(
                    f"{type(self).__name__}('{self.name}'): "
                    "exp waveform requires td1 and tau1"
                )
            self.v1 = cas.sympify(self.v1)
            self.v2 = cas.sympify(self.v2)
            self.td1 = cas.sympify(self.td1)
            self.tau1 = cas.sympify(self.tau1)
            if self.td2 is not None:
                self.td2 = cas.sympify(self.td2)
            if self.tau2 is not None:
                self.tau2 = cas.sympify(self.tau2)
        else:
            raise ValueError(
                f"{type(self).__name__}('{self.name}'): "
                f"unknown waveform {self.waveform!r}"
            )

    def _source_value(self, ctx: StampContext) -> cas.Expr:
        wf = self.waveform
        # Transient: Laplace transform of the waveform; a plain DC value
        # is a step switched on at t = 0 (value/s). ``ac_value`` is an
        # AC-phasor concept and is ignored here.
        if ctx.mode == "tran":
            return waveform_laplace(self, ctx.s)
        if ctx.mode == "ac":
            # Waveform-as-AC is legacy compatibility; solve_transient()
            # is the intended API for time-domain responses.
            if wf is not None:
                return waveform_laplace(self, ctx.s)
            if self.ac_value is not None:
                return self.ac_value
        # DC: for pulsed waveforms v1 is the DC level; otherwise use value.
        if ctx.mode == "dc" and wf in ("pulse", "exp"):
            assert self.v1 is not None
            return self.v1
        return self.value

    def stamp(self, ctx: StampContext) -> None:
        aux = ctx.aux(self.name)
        i, j = ctx.n(self.n_plus), ctx.n(self.n_minus)
        if i >= 0:
            ctx.A[i, aux] += 1
            ctx.A[aux, i] += 1
        if j >= 0:
            ctx.A[j, aux] -= 1
            ctx.A[aux, j] -= 1
        ctx.b[aux] = self._source_value(ctx)
