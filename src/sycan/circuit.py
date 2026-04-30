"""Netlist container.

Node ``"0"`` is always ground. Components are added either in bulk via
:meth:`Circuit.add` or through typed convenience methods that mirror
SPICE letters (``add_resistor``, ``add_vsource``, ``add_vcvs`` ...).

``Circuit.add`` reads a component's circuit nodes through the unified
:attr:`~sycan.mna.Component.ports` interface — it does not know about
specific component types. The catalog of available component classes is
populated at import time via :mod:`sycan.components` and exposed by
:meth:`Circuit.available_components`.
"""
from __future__ import annotations

from typing import Optional

import sycan.components  # noqa: F401  -- triggers component auto-discovery
from sycan.mna import Component, Value
from sycan.components.active import (
    BJT,
    Diode,
    NMOS_3T,
    NMOS_4T,
    NMOS_L1,
    NMOS_subthreshold,
    PMOS_3T,
    PMOS_4T,
    PMOS_L1,
    PMOS_subthreshold,
    Triode,
)
from sycan.components.blocks import (
    Gain,
    Integrator,
    Quantizer,
    Summer,
    TransferFunction,
)
from sycan.components.rf import TLINE
from sycan.components.basic import (
    CCCS,
    CCVS,
    Capacitor,
    CurrentSource,
    GND,
    Inductor,
    Port,
    Resistor,
    VCCS,
    VCVS,
    VoltageSource,
)


class Circuit:
    """Symbolic netlist."""

    def __init__(self, name: str = "circuit") -> None:
        self.name = name
        self.components: list[Component] = []
        self._nodes: dict[str, int] = {"0": 0}

    def _touch(self, node: str) -> None:
        if node not in self._nodes:
            self._nodes[node] = len(self._nodes)

    def add(self, component: Component) -> Component:
        """Append a pre-built component, registering its referenced nodes.

        Nodes are read through the component's
        :attr:`~sycan.mna.Component.ports` declaration, so any new
        :class:`Component` subclass works without changes here.
        """
        for node in component.iter_node_names():
            self._touch(node)
        self.components.append(component)
        return component

    @staticmethod
    def available_components() -> dict[str, type[Component]]:
        """Return a name → class map of every registered component type."""
        return Component.available()

    def add_resistor(self, name: str, n_plus: str, n_minus: str, value: Value) -> Resistor:
        return self.add(Resistor(name, n_plus, n_minus, value))  # type: ignore[return-value]

    def add_inductor(self, name: str, n_plus: str, n_minus: str, value: Value) -> Inductor:
        return self.add(Inductor(name, n_plus, n_minus, value))  # type: ignore[return-value]

    def add_capacitor(self, name: str, n_plus: str, n_minus: str, value: Value) -> Capacitor:
        return self.add(Capacitor(name, n_plus, n_minus, value))  # type: ignore[return-value]

    def add_vsource(
        self,
        name: str,
        n_plus: str,
        n_minus: str,
        value: Value,
        ac_value: Optional[Value] = None,
    ) -> VoltageSource:
        return self.add(VoltageSource(name, n_plus, n_minus, value, ac_value))  # type: ignore[return-value]

    def add_isource(
        self,
        name: str,
        n_plus: str,
        n_minus: str,
        value: Value,
        ac_value: Optional[Value] = None,
    ) -> CurrentSource:
        return self.add(CurrentSource(name, n_plus, n_minus, value, ac_value))  # type: ignore[return-value]

    def add_vcvs(
        self,
        name: str,
        n_plus: str,
        n_minus: str,
        nc_plus: str,
        nc_minus: str,
        gain: Value,
    ) -> VCVS:
        return self.add(VCVS(name, n_plus, n_minus, nc_plus, nc_minus, gain))  # type: ignore[return-value]

    def add_vccs(
        self,
        name: str,
        n_plus: str,
        n_minus: str,
        nc_plus: str,
        nc_minus: str,
        gain: Value,
    ) -> VCCS:
        return self.add(VCCS(name, n_plus, n_minus, nc_plus, nc_minus, gain))  # type: ignore[return-value]

    def add_cccs(
        self, name: str, n_plus: str, n_minus: str, ctrl: str, gain: Value
    ) -> CCCS:
        return self.add(CCCS(name, n_plus, n_minus, ctrl, gain))  # type: ignore[return-value]

    def add_ccvs(
        self, name: str, n_plus: str, n_minus: str, ctrl: str, gain: Value
    ) -> CCVS:
        return self.add(CCVS(name, n_plus, n_minus, ctrl, gain))  # type: ignore[return-value]

    def add_port(
        self,
        name: str,
        n_plus: str,
        n_minus: str = "0",
        role: str = "generic",
    ) -> Port:
        """Mark ``(n_plus, n_minus)`` as a named port for impedance analysis."""
        return self.add(Port(name, n_plus, n_minus, role))  # type: ignore[return-value]

    def add_gnd(self, name: str, node: str) -> GND:
        """Tie ``node`` to the absolute zero reference."""
        return self.add(GND(name, node))  # type: ignore[return-value]

    def add_triode(
        self,
        name: str,
        plate: str,
        grid: str,
        cathode: str,
        K: Value,
        mu: Value,
        **kwargs: Value,
    ) -> Triode:
        """Attach a Langmuir 3/2-power vacuum-tube triode.

        Optional keywords: ``V_g_op`` / ``V_p_op`` (AC operating point),
        ``C_gk`` / ``C_gp`` / ``C_pk`` (intrinsic capacitances).
        """
        return self.add(
            Triode(name, plate, grid, cathode, K, mu, **kwargs)
        )  # type: ignore[return-value]

    def add_tline(
        self,
        name: str,
        n_in_p: str,
        n_in_m: str,
        n_out_p: str,
        n_out_m: str,
        Z0: Value,
        td: Value,
    ) -> TLINE:
        """Attach a lossless 2-port transmission line (Z0, one-way delay td)."""
        return self.add(
            TLINE(name, n_in_p, n_in_m, n_out_p, n_out_m, Z0, td)
        )  # type: ignore[return-value]

    def add_diode(
        self,
        name: str,
        anode: str,
        cathode: str,
        IS: Value,
        N: Optional[Value] = None,
        V_T: Optional[Value] = None,
    ) -> Diode:
        """Attach a Shockley diode: ``I_D = IS (exp(V_D/(N V_T)) - 1)``."""
        kwargs: dict[str, Value] = {}
        if N is not None:
            kwargs["N"] = N
        if V_T is not None:
            kwargs["V_T"] = V_T
        return self.add(Diode(name, anode, cathode, IS, **kwargs))  # type: ignore[return-value]

    def add_bjt(
        self,
        name: str,
        collector: str,
        base: str,
        emitter: str,
        polarity: str,
        IS: Value,
        BF: Value,
        BR: Value,
        **kwargs: Value,
    ) -> BJT:
        """Attach a Gummel-Poon DC BJT (``polarity='NPN'`` or ``'PNP'``).

        Optional SPICE G-P parameters (``NF``, ``NR``, ``VAF``, ``VAR``,
        ``IKF``, ``IKR``, ``ISE``, ``NE``, ``ISC``, ``NC``, ``V_T``)
        can be supplied as keyword arguments.
        """
        return self.add(
            BJT(name, collector, base, emitter, polarity, IS, BF, BR, **kwargs)
        )  # type: ignore[return-value]

    def add_nmos_l1(
        self,
        name: str,
        drain: str,
        gate: str,
        source: str,
        mu_n: Value,
        Cox: Value,
        W: Value,
        L: Value,
        V_TH: Value,
        **kwargs: Value,
    ) -> NMOS_L1:
        """Attach a Shichman-Hodges (Level 1) NMOS.

        Optional keyword parameters: ``lam`` (channel-length modulation),
        ``C_gs``, ``C_gd`` (intrinsic capacitances), and
        ``V_GS_op`` / ``V_DS_op`` (AC linearisation point).
        """
        return self.add(
            NMOS_L1(name, drain, gate, source, mu_n, Cox, W, L, V_TH, **kwargs)
        )  # type: ignore[return-value]

    def add_pmos_l1(
        self,
        name: str,
        drain: str,
        gate: str,
        source: str,
        mu_n: Value,
        Cox: Value,
        W: Value,
        L: Value,
        V_TH: Value,
        **kwargs: Value,
    ) -> PMOS_L1:
        """Attach a Shichman-Hodges (Level 1) PMOS (V_TH is a magnitude)."""
        return self.add(
            PMOS_L1(name, drain, gate, source, mu_n, Cox, W, L, V_TH, **kwargs)
        )  # type: ignore[return-value]

    def add_nmos_subthreshold(
        self,
        name: str,
        drain: str,
        gate: str,
        source: str,
        mu_n: Value,
        Cox: Value,
        W: Value,
        L: Value,
        V_TH: Value,
        m: Optional[Value] = None,
        V_T: Optional[Value] = None,
    ) -> NMOS_subthreshold:
        """Attach a sub-threshold NMOS."""
        kwargs: dict[str, Value] = {}
        if m is not None:
            kwargs["m"] = m
        if V_T is not None:
            kwargs["V_T"] = V_T
        return self.add(
            NMOS_subthreshold(
                name, drain, gate, source, mu_n, Cox, W, L, V_TH, **kwargs
            )
        )  # type: ignore[return-value]

    def add_pmos_subthreshold(
        self,
        name: str,
        drain: str,
        gate: str,
        source: str,
        mu_n: Value,
        Cox: Value,
        W: Value,
        L: Value,
        V_TH: Value,
        m: Optional[Value] = None,
        V_T: Optional[Value] = None,
    ) -> PMOS_subthreshold:
        """Attach a sub-threshold PMOS (V_TH is a magnitude)."""
        kwargs: dict[str, Value] = {}
        if m is not None:
            kwargs["m"] = m
        if V_T is not None:
            kwargs["V_T"] = V_T
        return self.add(
            PMOS_subthreshold(
                name, drain, gate, source, mu_n, Cox, W, L, V_TH, **kwargs
            )
        )  # type: ignore[return-value]

    def add_nmos_3t(
        self,
        name: str,
        drain: str,
        gate: str,
        source: str,
        mu_n: Value,
        Cox: Value,
        W: Value,
        L: Value,
        V_TH: Value,
        **kwargs: Value,
    ) -> NMOS_3T:
        """Attach a segmented L1 + matched-weak-inversion NMOS.

        Optional keyword parameters: ``lam`` (channel-length modulation),
        ``m`` (sub-threshold slope factor), ``V_T`` (thermal voltage),
        ``C_gs``, ``C_gd`` (intrinsic capacitances), and
        ``V_GS_op`` / ``V_DS_op`` (AC linearisation point).
        """
        return self.add(
            NMOS_3T(name, drain, gate, source, mu_n, Cox, W, L, V_TH, **kwargs)
        )  # type: ignore[return-value]

    def add_pmos_3t(
        self,
        name: str,
        drain: str,
        gate: str,
        source: str,
        mu_n: Value,
        Cox: Value,
        W: Value,
        L: Value,
        V_TH: Value,
        **kwargs: Value,
    ) -> PMOS_3T:
        """Attach a segmented L1 + matched-weak-inversion PMOS
        (``V_TH`` is a positive magnitude)."""
        return self.add(
            PMOS_3T(name, drain, gate, source, mu_n, Cox, W, L, V_TH, **kwargs)
        )  # type: ignore[return-value]

    def add_nmos_4t(
        self,
        name: str,
        drain: str,
        gate: str,
        source: str,
        bulk: str,
        mu_n: Value,
        Cox: Value,
        W: Value,
        L: Value,
        V_TH0: Value,
        **kwargs: Value,
    ) -> NMOS_4T:
        """Attach a four-terminal segmented NMOS (body-effect aware).

        Optional keyword parameters: ``lam`` (channel-length modulation),
        ``gamma`` (body-effect coefficient, V^0.5), ``phi`` (surface
        potential 2 φ_F), ``m`` (sub-threshold slope factor), ``V_T``
        (thermal voltage), ``C_gs``, ``C_gd``, and the AC linearisation
        points ``V_GS_op`` / ``V_DS_op`` / ``V_BS_op``.
        """
        return self.add(
            NMOS_4T(
                name, drain, gate, source, bulk,
                mu_n, Cox, W, L, V_TH0, **kwargs,
            )
        )  # type: ignore[return-value]

    def add_pmos_4t(
        self,
        name: str,
        drain: str,
        gate: str,
        source: str,
        bulk: str,
        mu_n: Value,
        Cox: Value,
        W: Value,
        L: Value,
        V_TH0: Value,
        **kwargs: Value,
    ) -> PMOS_4T:
        """Attach a four-terminal segmented PMOS (body-effect aware,
        ``V_TH0`` is a positive magnitude)."""
        return self.add(
            PMOS_4T(
                name, drain, gate, source, bulk,
                mu_n, Cox, W, L, V_TH0, **kwargs,
            )
        )  # type: ignore[return-value]

    def add_transfer_function(
        self,
        name: str,
        in_p: str,
        in_m: str,
        out_p: str,
        out_m: str,
        H: Value,
        var: Optional[Value] = None,
        dc_gain: Optional[Value] = None,
    ) -> TransferFunction:
        """Attach a generic LTI block ``V(out) = H(s) * V(in)``."""
        kwargs: dict = {}
        if var is not None:
            kwargs["var"] = var
        if dc_gain is not None:
            kwargs["dc_gain"] = dc_gain
        return self.add(
            TransferFunction(name, in_p, in_m, out_p, out_m, H, **kwargs)
        )  # type: ignore[return-value]

    def add_integrator(
        self,
        name: str,
        in_p: str,
        in_m: str,
        out_p: str,
        out_m: str,
        k: Value = 1,
        leak: Value = 0,
    ) -> Integrator:
        """Attach a continuous-time integrator ``H(s) = k / (s + leak)``."""
        return self.add(
            Integrator(name, in_p, in_m, out_p, out_m, k=k, leak=leak)
        )  # type: ignore[return-value]

    def add_gain(
        self,
        name: str,
        in_p: str,
        in_m: str,
        out_p: str,
        out_m: str,
        k: Value,
    ) -> Gain:
        """Attach a static gain ``V(out) = k * V(in)``."""
        return self.add(Gain(name, in_p, in_m, out_p, out_m, k))  # type: ignore[return-value]

    def add_summer(
        self,
        name: str,
        out_p: str,
        out_m: str,
        inputs: list,
    ) -> Summer:
        """Attach a weighted summing junction.

        ``inputs`` is a list of ``(in_p, in_m, weight)`` tuples or
        ``(node, weight)`` 2-tuples for inputs referenced to ground.
        """
        return self.add(Summer(name, out_p, out_m, inputs))  # type: ignore[return-value]

    def add_quantizer(
        self,
        name: str,
        in_p: str,
        in_m: str,
        out_p: str,
        out_m: str,
        k_q: Value = 1,
        qnoise: Optional[Value] = None,
    ) -> Quantizer:
        """Attach a linear-model quantizer ``V(out) = k_q * V(in) + V_q``.

        ``qnoise`` overrides the additive-noise sympy symbol; pass
        ``0`` to model an ideal noiseless gain.
        """
        return self.add(
            Quantizer(name, in_p, in_m, out_p, out_m, k_q=k_q, qnoise=qnoise)
        )  # type: ignore[return-value]

    @property
    def nodes(self) -> list[str]:
        """Non-ground node names ordered by MNA index."""
        return [
            name
            for name, i in sorted(self._nodes.items(), key=lambda kv: kv[1])
            if name != "0"
        ]
