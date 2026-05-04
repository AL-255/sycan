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
    OPAMP,
    OPAMP1,
    Quantizer,
    SubCircuit,
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
    MutualCoupling,
    Port,
    Resistor,
    VCCS,
    VCVS,
    VoltageSource,
)


_WAVEFORM_KEYS = frozenset({
    "waveform", "amplitude", "frequency", "phase",
    "v1", "v2", "td", "pw", "td1", "tau1", "td2", "tau2",
})


def _waveform_kwargs(local_vars: dict) -> dict:
    """Extract waveform-related kwargs from a locals() dict."""
    return {k: local_vars[k] for k in _WAVEFORM_KEYS if local_vars.get(k) is not None}


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

    def flat_components(self) -> list[Component]:
        """All leaf components, recursively expanding any :class:`SubCircuit`.

        :class:`SubCircuit` instances are flattened into renamed,
        node-rerouted clones of their bodies (see
        :meth:`SubCircuit.expand_leaves`). Non-subcircuit components are
        passed through unchanged. Used by the MNA build / solve path so
        that hierarchical designs are stamped as if hand-inlined.
        """
        out: list[Component] = []
        for c in self.components:
            if isinstance(c, SubCircuit):
                out.extend(c.expand_leaves())
            else:
                out.append(c)
        return out

    def subcircuits(self) -> list[SubCircuit]:
        """Top-level :class:`SubCircuit` instances in this circuit."""
        return [c for c in self.components if isinstance(c, SubCircuit)]

    def print_hierarchy(self, file=None) -> None:
        """Print the design's component tree, expanding subcircuits.

        Output has three sections:

        1. A header naming the top-level circuit.
        2. A summary listing each :class:`SubCircuit` instance found at
           any depth, grouped by class, with its dotted instance path.
        3. A box-drawn tree of every component, with subcircuit bodies
           shown as nested branches under their instance node.
        """
        import sys

        out = file if file is not None else sys.stdout

        print(f"Circuit {self.name!r}", file=out)

        # Section: subcircuit summary (recursive across all depths).
        subs = self._collect_subcircuits()
        if subs:
            print("", file=out)
            print(f"Subcircuits ({len(subs)} total):", file=out)
            by_class: dict[str, list[str]] = {}
            for path, sc in subs:
                by_class.setdefault(type(sc).__name__, []).append(path)
            for cls_name in sorted(by_class):
                paths = by_class[cls_name]
                print(f"  {cls_name}  x{len(paths)}", file=out)
                for path in paths:
                    print(f"    - {path}", file=out)
        else:
            print("", file=out)
            print("Subcircuits: (none)", file=out)

        # Section: full component tree.
        print("", file=out)
        print("Tree:", file=out)
        self._print_tree(self.components, prefix="", file=out)

    def _collect_subcircuits(
        self, prefix: str = "", components: Optional[list[Component]] = None
    ) -> list[tuple[str, SubCircuit]]:
        """Walk the hierarchy and return ``(dotted_path, instance)`` pairs."""
        if components is None:
            components = self.components
        found: list[tuple[str, SubCircuit]] = []
        for c in components:
            if isinstance(c, SubCircuit):
                path = f"{prefix}{c.name}" if not prefix else f"{prefix}.{c.name}"
                found.append((path, c))
                found.extend(
                    self._collect_subcircuits(
                        prefix=path, components=c.body.components
                    )
                )
        return found

    def _print_tree(
        self, components: list[Component], prefix: str, file
    ) -> None:
        n = len(components)
        for i, c in enumerate(components):
            is_last = i == n - 1
            connector = "└── " if is_last else "├── "
            print(f"{prefix}{connector}{self._fmt_component(c)}", file=file)
            if isinstance(c, SubCircuit):
                child_prefix = prefix + ("    " if is_last else "│   ")
                self._print_tree(c.body.components, child_prefix, file)

    @staticmethod
    def _fmt_component(c: Component) -> str:
        cls = type(c).__name__
        if isinstance(c, SubCircuit):
            pins = ", ".join(
                f"{pin}={node}" for pin, node in c.port_map.items()
            )
            return f"{c.name} [{cls}]  ({pins})"
        nodes = ", ".join(
            str(getattr(c, attr))
            for attr in c.ports
            if getattr(c, attr, None) is not None
        )
        return f"{c.name} [{cls}]  ({nodes})"

    def add_subcircuit(
        self,
        name: str,
        body: "Circuit",
        port_map: dict[str, str],
    ) -> SubCircuit:
        """Add a generic subcircuit instance wrapping ``body``."""
        return self.add(SubCircuit(name=name, body=body, port_map=port_map))  # type: ignore[return-value]

    def add_opamp(
        self,
        name: str,
        in_p: str,
        in_n: str,
        out: str,
        A: Optional[Value] = None,
    ) -> OPAMP:
        """Add an ideal differential op-amp (:class:`OPAMP` subcircuit)."""
        return self.add(OPAMP(name, in_p, in_n, out, A))  # type: ignore[return-value]

    def add_opamp1(
        self,
        name: str,
        in_p: str,
        in_n: str,
        out: str,
        A: Optional[Value] = None,
        GBW: Optional[Value] = None,
        Z_out: Optional[Value] = None,
    ) -> OPAMP1:
        """Add a first-order op-amp with finite GBW and output impedance.

        See :class:`~sycan.components.blocks.opamp.OPAMP1`.
        """
        return self.add(OPAMP1(name, in_p, in_n, out, A, GBW, Z_out))  # type: ignore[return-value]

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
        waveform: Optional[str] = None,
        amplitude: Optional[Value] = None,
        frequency: Optional[Value] = None,
        phase: Optional[Value] = None,
        # pulse / exp waveform parameters
        v1: Optional[Value] = None,
        v2: Optional[Value] = None,
        td: Optional[Value] = None,
        pw: Optional[Value] = None,
        td1: Optional[Value] = None,
        tau1: Optional[Value] = None,
        td2: Optional[Value] = None,
        tau2: Optional[Value] = None,
    ) -> VoltageSource:
        """Add an independent voltage source, optionally with a waveform mode.

        Supported waveforms: ``"sine"``, ``"pulse"``, ``"exp"``.  See
        :class:`~sycan.components.basic.voltage_source.VoltageSource`.
        """
        kwargs = _waveform_kwargs(locals())
        return self.add(
            VoltageSource(name, n_plus, n_minus, value, ac_value, **kwargs)
        )  # type: ignore[return-value]

    def add_isource(
        self,
        name: str,
        n_plus: str,
        n_minus: str,
        value: Value,
        ac_value: Optional[Value] = None,
        waveform: Optional[str] = None,
        amplitude: Optional[Value] = None,
        frequency: Optional[Value] = None,
        phase: Optional[Value] = None,
        v1: Optional[Value] = None,
        v2: Optional[Value] = None,
        td: Optional[Value] = None,
        pw: Optional[Value] = None,
        td1: Optional[Value] = None,
        tau1: Optional[Value] = None,
        td2: Optional[Value] = None,
        tau2: Optional[Value] = None,
    ) -> CurrentSource:
        """Add an independent current source, optionally with a waveform mode.

        Supported waveforms: ``"sine"``, ``"pulse"``, ``"exp"``.  See
        :class:`~sycan.components.basic.current_source.CurrentSource`.
        """
        kwargs = _waveform_kwargs(locals())
        return self.add(
            CurrentSource(name, n_plus, n_minus, value, ac_value, **kwargs)
        )  # type: ignore[return-value]

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

    def add_mutual_coupling(
        self,
        name: str,
        inductors: list[str],
        k: Value = 1,
    ) -> MutualCoupling:
        """Add mutual inductance coupling between ``inductors``.

        Parameters
        ----------
        name
            Coupling designator (e.g. ``"K1"``).
        inductors
            List of inductor designators to couple.
        k
            Coupling coefficient (default 1 = perfect coupling).

        Inductor values are resolved lazily at MNA-build time, so ``K``
        may precede the ``L`` elements it references.
        """
        kc = MutualCoupling(name, k=k)
        for ind_name in inductors:
            kc.couple(ind_name)
        return self.add(kc)  # type: ignore[return-value]

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
        loss: Value = 0,
    ) -> TLINE:
        """Attach a transmission line (Z0, delay td, optional loss in nepers)."""
        return self.add(
            TLINE(name, n_in_p, n_in_m, n_out_p, n_out_m, Z0, td, loss)
        )  # type: ignore[return-value]

    def add_diode(
        self,
        name: str,
        anode: str,
        cathode: str,
        IS: Value,
        N: Optional[Value] = None,
        V_T: Optional[Value] = None,
        C_j: Optional[Value] = None,
        V_D_op: Optional[Value] = None,
    ) -> Diode:
        """Attach a Shockley diode: ``I_D = IS (exp(V_D/(N V_T)) - 1)``.

        Optional ``C_j`` adds junction capacitance in AC analysis.
        Optional ``V_D_op`` pins the DC operating-point voltage for
        small-signal linearisation.
        """
        kwargs: dict[str, Value] = {}
        if N is not None:
            kwargs["N"] = N
        if V_T is not None:
            kwargs["V_T"] = V_T
        if C_j is not None:
            kwargs["C_j"] = C_j
        if V_D_op is not None:
            kwargs["V_D_op"] = V_D_op
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
        """Attach a Gummel-Poon BJT (``polarity='NPN'`` or ``'PNP'``).

        Optional G-P parameters: ``NF``, ``NR``, ``VAF``, ``VAR``,
        ``IKF``, ``IKR``, ``ISE``, ``NE``, ``ISC``, ``NC``, ``V_T``,
        and AC model capacitances ``C_pi``, ``C_mu``.
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
