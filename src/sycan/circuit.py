"""Netlist container.

Node ``"0"`` is always ground. Components are added either in bulk via
:meth:`Circuit.add` or through typed convenience methods that mirror
SPICE letters (``add_resistor``, ``add_vsource``, ``add_vcvs`` ...).
"""
from __future__ import annotations

from typing import Optional

from sycan.mna import Component, Value
from sycan.components.active import (
    BJT,
    Diode,
    NMOS_L1,
    NMOS_subthreshold,
    PMOS_L1,
    PMOS_subthreshold,
)
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
        """Append a pre-built component, registering its referenced nodes."""
        for attr in (
            "n_plus",
            "n_minus",
            "nc_plus",
            "nc_minus",
            "node",
            "drain",
            "gate",
            "source",
            "collector",
            "base",
            "emitter",
            "anode",
            "cathode",
        ):
            node = getattr(component, attr, None)
            if node is not None:
                self._touch(node)
        self.components.append(component)
        return component

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

    @property
    def nodes(self) -> list[str]:
        """Non-ground node names ordered by MNA index."""
        return [
            name
            for name, i in sorted(self._nodes.items(), key=lambda kv: kv[1])
            if name != "0"
        ]
