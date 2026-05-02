"""Hierarchical subcircuit (SPICE ``X``).

A :class:`SubCircuit` wraps an inner :class:`~sycan.circuit.Circuit`
(the *body*) and exposes a subset of its internal nodes as external
*pins* via :attr:`SubCircuit.port_map`. When the parent circuit is
solved, every :class:`SubCircuit` is recursively *flattened*: each
leaf component inside the body is cloned with

* its name prefixed by the instance name (``X1.E1``);
* every node attribute rewritten — pins map to the parent-scope node
  given by ``port_map``, internal-only nodes are namespaced
  ``<instance>.<inner>``, and ground (``"0"``) is left untouched.

The clones then stamp into the parent MNA exactly as if the user had
inlined them. Auxiliary branch-currents and nonlinear residuals come
along for free because each clone reports its own ``aux_count`` /
``has_nonlinear`` flags.

Subcircuits can nest — a body may itself contain :class:`SubCircuit`
instances, and the rewriting is composed at expansion time.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, ClassVar, Iterator, Mapping, Optional

from sycan.mna import Component, NoiseSpec, StampContext

if TYPE_CHECKING:
    from sycan.circuit import Circuit


@dataclass
class SubCircuit(Component):
    """Hierarchical subcircuit instance (SPICE ``X`` element).

    Parameters
    ----------
    name
        Instance designator (e.g. ``"X1"``).
    body
        Inner :class:`Circuit` defining the subcircuit's contents. The
        body must already contain the components it needs at construction
        time so that pin validation can run.
    port_map
        ``{body_pin: parent_node}`` — keys are node names that exist in
        ``body``; values are the parent-scope nodes the pins connect to.
    """

    name: str
    body: "Circuit"
    port_map: Mapping[str, str]
    include_noise: NoiseSpec = field(default=None, kw_only=True)

    has_aux: ClassVar[bool] = False
    SUPPORTED_NOISE: ClassVar[frozenset[str]] = frozenset()

    def __post_init__(self) -> None:
        from sycan.circuit import Circuit
        if not isinstance(self.body, Circuit):
            raise TypeError(
                f"SubCircuit {self.name!r}: body must be a Circuit, "
                f"got {type(self.body).__name__}"
            )
        self.port_map = dict(self.port_map)
        body_nodes = set(self.body._nodes.keys())
        missing = [pin for pin in self.port_map if pin not in body_nodes]
        if missing:
            raise ValueError(
                f"SubCircuit {self.name!r}: pin(s) {missing!r} not defined "
                f"in body (known body nodes: {sorted(body_nodes)!r})"
            )
        self.include_noise = self._normalize_noise(self.include_noise)

    # --- Component protocol overrides ---------------------------------

    def iter_node_names(self) -> Iterator[str]:
        """Every node name the parent must register for this subcircuit.

        Includes both the parent-scope endpoints (``port_map.values()``)
        and the namespaced internal nodes that the flattened leaves will
        reference.
        """
        seen: set[str] = set()
        for leaf in self.expand_leaves():
            for attr in leaf.ports:
                node = getattr(leaf, attr, None)
                if node is None or node == "0":
                    continue
                if node in seen:
                    continue
                seen.add(node)
                yield node

    def aux_count(self, mode: str) -> int:
        # Aux rows are owned by the flattened leaves directly.
        return 0

    def stamp(self, ctx: StampContext) -> None:
        # Stamping is delegated to the flattened leaves; the SubCircuit
        # element itself contributes nothing to MNA.
        return None

    # --- Hierarchy navigation -----------------------------------------

    def child_subcircuits(self) -> list["SubCircuit"]:
        """Direct child :class:`SubCircuit` instances inside this body."""
        return [c for c in self.body.components if isinstance(c, SubCircuit)]

    def expand_leaves(
        self,
        name_prefix: Optional[str] = None,
        node_remap: Optional[dict[str, str]] = None,
    ) -> list[Component]:
        """Return the recursively-flattened leaf components.

        ``name_prefix`` and ``node_remap`` are used internally when this
        subcircuit is itself nested inside another; callers normally
        omit them.
        """
        if name_prefix is None:
            name_prefix = self.name
        if node_remap is None:
            node_remap = dict(self.port_map)

        def resolve(inner_node: str) -> str:
            if inner_node == "0":
                return "0"
            if inner_node in node_remap:
                return node_remap[inner_node]
            return f"{name_prefix}.{inner_node}"

        leaves: list[Component] = []
        for inner in self.body.components:
            if isinstance(inner, SubCircuit):
                nested_remap = {
                    pin: resolve(conn) for pin, conn in inner.port_map.items()
                }
                leaves.extend(
                    inner.expand_leaves(
                        name_prefix=f"{name_prefix}.{inner.name}",
                        node_remap=nested_remap,
                    )
                )
            else:
                new_attrs: dict[str, object] = {
                    "name": f"{name_prefix}.{inner.name}",
                }
                for port_attr in inner.ports:
                    node = getattr(inner, port_attr, None)
                    if node is not None:
                        new_attrs[port_attr] = resolve(node)
                leaves.append(replace(inner, **new_attrs))
        return leaves
