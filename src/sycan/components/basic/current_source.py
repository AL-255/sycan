"""Independent current source (SPICE ``I``).

Supports DC, AC-phasor, and waveform modes: ``"sine"``, ``"pulse"``,
``"exp"``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Optional

from sycan import cas as cas

from sycan.mna import Component, NoiseSpec, StampContext
from sycan.components.basic.voltage_source import (
    _sine_laplace, _pulse_laplace, _exp_laplace,
)


@dataclass
class CurrentSource(Component):
    """Ideal current source.

    Drives current ``value`` from ``n_plus`` to ``n_minus`` internally,
    so externally the source pulls current out of ``n_plus`` and injects
    it into ``n_minus`` (SPICE convention).

    ``ac_value`` is the small-signal phasor used in AC analysis; if
    ``None``, AC analysis reuses ``value``. Ideal sources are noiseless;
    ``include_noise`` is accepted for interface uniformity.

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
        if ctx.mode == "ac":
            if wf == "sine":
                assert self.amplitude is not None and self.frequency is not None
                omega = 2 * cas.pi * self.frequency
                return _sine_laplace(ctx.s, self.amplitude, omega, self.phase)
            if wf == "pulse":
                assert self.v1 is not None and self.v2 is not None
                return _pulse_laplace(ctx.s, self.v1, self.v2, self.td, self.pw)
            if wf == "exp":
                assert self.v1 is not None and self.v2 is not None
                return _exp_laplace(ctx.s, self.v1, self.v2,
                                    self.td1, self.tau1, self.td2, self.tau2)
            if self.ac_value is not None:
                return self.ac_value
        if ctx.mode == "dc" and wf in ("pulse", "exp"):
            assert self.v1 is not None
            return self.v1
        return self.value

    def stamp(self, ctx: StampContext) -> None:
        i, j = ctx.n(self.n_plus), ctx.n(self.n_minus)
        val = self._source_value(ctx)
        if i >= 0:
            ctx.b[i] -= val
        if j >= 0:
            ctx.b[j] += val
