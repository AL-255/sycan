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
    NJFET,
    NMOS_3T,
    NMOS_4T,
    NMOS_L1,
    NMOS_subthreshold,
    PJFET,
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
    BehavioralCurrent,
    BehavioralVoltage,
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
    VSwitch,
    Varactor,
    VoltageSource,
)


def print_hierarchy(circuit: "Circuit", file=None) -> None:
    """Print ``circuit``'s hierarchy tree to ``file`` (or stdout).

    Convenience wrapper for :meth:`Circuit.print_hierarchy` so callers
    can write ``sycan.print_hierarchy(c)`` symmetrically with the rest
    of the top-level analysis API.
    """
    circuit.print_hierarchy(file=file)


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
        # Assumptions attached to the circuit are applied (and checked)
        # by the unified solver. Imported lazily inside ``assume`` /
        # ``check_assumptions`` to avoid a top-level import cycle with
        # the assumptions module (which references Circuit for typing).
        self.assumptions: list = []

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

    def flat_components(
        self,
        collapse_paths: Optional[frozenset[tuple[str, ...]]] = None,
    ) -> list:
        """All leaf components, recursively expanding any :class:`SubCircuit`.

        :class:`SubCircuit` instances are flattened into renamed,
        node-rerouted clones of their bodies (see
        :meth:`SubCircuit.expand_leaves`). Non-subcircuit components are
        passed through unchanged. Used by the MNA build / solve path so
        that hierarchical designs are stamped as if hand-inlined.

        ``collapse_paths`` is an optional set of fully-qualified
        :class:`SubCircuit` instance paths (tuples) that should be
        replaced by a single ``_CollapsedGroup`` placeholder instead of
        being expanded. Intended for use by visualisation code that
        wants to hide implementation detail; the placeholders are not
        valid MNA components, so callers passing ``collapse_paths``
        must not feed the result back into the solver.
        """
        out: list = []
        for c in self.components:
            if isinstance(c, SubCircuit):
                out.extend(c.expand_leaves(collapse_paths=collapse_paths))
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
            extra = ""
            if getattr(c, "params", None):
                params_str = ", ".join(
                    f"{k}={v}" for k, v in c.params.items()
                )
                extra = f"  PARAMS: {params_str}"
            return f"{c.name} [{cls}]  ({pins}){extra}"
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
        params: Optional[dict[str, Value]] = None,
    ) -> SubCircuit:
        """Add a generic subcircuit instance wrapping ``body``.

        ``params`` (optional) maps parameter names to values that
        substitute matching ``cas.Symbol(name)`` placeholders inside
        the body when it is flattened. Outer params propagate into
        nested :class:`SubCircuit` instances unless those instances
        override the same key.
        """
        return self.add(
            SubCircuit(
                name=name, body=body, port_map=port_map, params=params or {}
            )
        )  # type: ignore[return-value]

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

    def add_varactor(
        self,
        name: str,
        n_plus: str,
        n_minus: str,
        C0: Value,
        V_J: Value = 0.7,
        M: Value = 0.5,
        V_op: Optional[Value] = None,
    ) -> Varactor:
        """Attach a junction-style voltage-controlled capacitor.

        See :class:`~sycan.components.basic.varactor.Varactor`.
        """
        return self.add(
            Varactor(name, n_plus, n_minus, C0, V_J, M, V_op=V_op)
        )  # type: ignore[return-value]

    def add_vswitch(
        self,
        name: str,
        n_plus: str,
        n_minus: str,
        nc_plus: str,
        nc_minus: str,
        R_on: Value = 1,
        R_off: Value = 1e9,
        V_t: Value = 0,
        V_h: Value = 0.1,
        V_c_op: Optional[Value] = None,
    ) -> VSwitch:
        """Attach a smooth voltage-controlled switch (SPICE ``S``)."""
        return self.add(
            VSwitch(
                name, n_plus, n_minus, nc_plus, nc_minus,
                R_on, R_off, V_t, V_h, V_c_op=V_c_op,
            )
        )  # type: ignore[return-value]

    def add_behavioral_current(
        self,
        name: str,
        n_plus: str,
        n_minus: str,
        expr: Value,
        V_op_subs: Optional[dict] = None,
    ) -> BehavioralCurrent:
        """Attach a behavioural current source ``I = expr``.

        ``expr`` may reference ``Symbol("V(<node>)")`` to access node
        voltages. ``V_op_subs`` is the operating-point map used for
        AC small-signal linearisation.
        """
        return self.add(
            BehavioralCurrent(name, n_plus, n_minus, expr, V_op_subs=V_op_subs)
        )  # type: ignore[return-value]

    def add_behavioral_voltage(
        self,
        name: str,
        n_plus: str,
        n_minus: str,
        expr: Value,
        V_op_subs: Optional[dict] = None,
    ) -> BehavioralVoltage:
        """Attach a behavioural voltage source ``V(+)-V(-) = expr``."""
        return self.add(
            BehavioralVoltage(name, n_plus, n_minus, expr, V_op_subs=V_op_subs)
        )  # type: ignore[return-value]

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

    def add_njfet(
        self,
        name: str,
        drain: str,
        gate: str,
        source: str,
        BETA: Value,
        VTO: Value,
        **kwargs: Value,
    ) -> NJFET:
        """Attach a Shichman-Hodges N-channel JFET (depletion-mode).

        ``VTO`` is stored as a positive magnitude (pinch-off magnitude).
        Optional keyword parameters: ``LAMBDA`` (channel-length modulation),
        ``C_gs``, ``C_gd`` (intrinsic capacitances), and
        ``V_GS_op`` / ``V_DS_op`` (AC linearisation point).
        """
        return self.add(
            NJFET(name, drain, gate, source, BETA, VTO, **kwargs)
        )  # type: ignore[return-value]

    def add_pjfet(
        self,
        name: str,
        drain: str,
        gate: str,
        source: str,
        BETA: Value,
        VTO: Value,
        **kwargs: Value,
    ) -> PJFET:
        """Attach a Shichman-Hodges P-channel JFET (depletion-mode).

        ``VTO`` is stored as a positive magnitude (pinch-off magnitude).
        Optional keyword parameters: ``LAMBDA`` (channel-length modulation),
        ``C_gs``, ``C_gd`` (intrinsic capacitances), and
        ``V_GS_op`` / ``V_DS_op`` (AC linearisation point).
        """
        return self.add(
            PJFET(name, drain, gate, source, BETA, VTO, **kwargs)
        )  # type: ignore[return-value]

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

    # ------------------------------------------------------------------
    # Assumption attachment
    # ------------------------------------------------------------------

    def assume(self, *assumptions) -> None:
        """Attach one or more :class:`~sycan.assumptions.Assumption`
        objects to this circuit.

        Attached assumptions are picked up automatically by the unified
        :func:`~sycan.solve` entry point — they're applied to the
        solution after the matrix solve, and (for region-style
        assumptions) verified by :meth:`check_assumptions` against the
        resulting operating point.
        """
        from sycan.assumptions import Assumption
        for a in assumptions:
            if not isinstance(a, Assumption):
                raise TypeError(
                    f"Circuit.assume: expected Assumption, got "
                    f"{type(a).__name__}"
                )
            self.assumptions.append(a)

    def assume_limit(self, symbol: "cas.Symbol", target: "cas.Expr") -> None:
        """Sugar for ``self.assume(Limit(symbol, target))``."""
        from sycan.assumptions import Limit
        self.assume(Limit(symbol=symbol, target=target))

    def assume_much_greater(self, big, small) -> None:
        """Sugar for ``self.assume(MuchGreater(big, small))`` — ``big >> small``."""
        from sycan.assumptions import MuchGreater
        self.assume(MuchGreater(big=big, small=small))

    def assume_much_less(self, small, big) -> None:
        """Sugar for ``self.assume(MuchLess(small, big))`` — ``small << big``."""
        from sycan.assumptions import MuchLess
        self.assume(MuchLess(small=small, big=big))

    def assume_region(self, component_name: str, region_name: str) -> None:
        """Sugar for ``self.assume(Region(component_name, region_name))``."""
        from sycan.assumptions import Region
        self.assume(Region(component=component_name, region=region_name))

    def check_assumptions(self, solution, extra: Optional[list] = None):
        """Verify every attached :class:`Assumption` against ``solution``.

        ``extra`` is an optional list of additional assumptions to
        verify in the same pass — useful for one-off checks that
        weren't attached to the circuit. Returns a list of
        :class:`~sycan.assumptions.CheckResult`.
        """
        from sycan.assumptions import check_assumptions as _check
        merged = list(self.assumptions) + list(extra or [])
        return _check(self, solution, merged)

    def group(
        self,
        components: list[Component],
        name: str,
        body_name: Optional[str] = None,
        params: Optional[dict[str, Value]] = None,
    ) -> SubCircuit:
        """Wrap an existing slice of this circuit's components in a SubCircuit.

        Replaces the listed components in place: they are removed from
        ``self.components`` and reattached inside a freshly created
        body :class:`Circuit`, with a new :class:`SubCircuit` instance
        inserted at the position of the first removed component.

        Pin selection is automatic: any node that the listed components
        reference and that is *also* used by something outside the
        group (or is the SPICE ground node ``"0"``) becomes an external
        pin. Internal-only nodes stay namespaced inside the body.

        Parameters
        ----------
        components
            The components to absorb. Must all currently belong to
            ``self.components``; order is preserved inside the body.
        name
            Designator for the new ``SubCircuit`` instance (e.g. ``"X1"``).
        body_name
            Optional name for the body :class:`Circuit`; defaults to
            ``name``.
        params
            Optional ``{symbol: value}`` map propagated into the body
            via the standard parameter mechanism.
        """
        if not components:
            raise ValueError("group: components list is empty")

        member_ids = {id(c) for c in components}
        if len(member_ids) != len(components):
            raise ValueError("group: components list contains duplicates")

        # Confirm every requested component is currently in this circuit.
        present_ids = {id(existing) for existing in self.components}
        for c in components:
            if id(c) not in present_ids:
                raise ValueError(
                    f"group: component {getattr(c, 'name', c)!r} is not in "
                    f"this circuit"
                )

        # Inventory node usage on both sides of the group boundary.
        inside_nodes: set[str] = set()
        for c in components:
            for node in c.iter_node_names():
                inside_nodes.add(node)

        outside_nodes: set[str] = set()
        for c in self.components:
            if id(c) in member_ids:
                continue
            for node in c.iter_node_names():
                outside_nodes.add(node)

        # External pins = nodes that cross the boundary or are ground.
        external = sorted(
            n for n in inside_nodes
            if n == "0" or n in outside_nodes
        )

        # Build the body and rewrite each member's external-node attrs
        # so the body sees pin-name nodes (we keep the same names — pin
        # name == external node name — which keeps SubCircuit.expand_leaves
        # happy and avoids touching internal-only nodes).
        body = Circuit(name=body_name or name)
        for c in components:
            body.add(c)

        port_map = {pin: pin for pin in external if pin != "0"}
        # Ground may appear inside without needing a pin; SubCircuit
        # treats "0" as universal across scopes.

        # Splice: remove members from self.components, insert new SubCircuit
        # at the position of the first removed one.
        first_idx = next(
            i for i, c in enumerate(self.components) if id(c) in member_ids
        )
        self.components = [
            c for c in self.components if id(c) not in member_ids
        ]
        sub = SubCircuit(
            name=name, body=body, port_map=port_map, params=params or {}
        )
        self.components.insert(first_idx, sub)
        # Rebuild the parent's node table from scratch so any internal-
        # only node that is now namespaced inside the body (e.g. ``inv``
        # → ``X1.inv``) drops out and the new namespaced names land in.
        # Otherwise stale entries in ``_nodes`` widen the MNA matrix
        # with zero rows and make it singular at solve time.
        self._nodes = {"0": 0}
        for c in self.components:
            for node in c.iter_node_names():
                self._touch(node)
        return sub
