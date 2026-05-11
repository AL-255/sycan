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

Parameter propagation
---------------------

A :class:`SubCircuit` may carry a ``params`` dict whose entries
substitute symbolic placeholders inside the body at expansion time.
The body's leaves reference parameters as plain ``cas.Symbol("R")``
expressions; the wrapper's ``params={"R": value, ...}`` replaces
those symbols on every cloned leaf. Nested subcircuits inherit the
outer scope's params automatically — a child's own ``params`` takes
precedence for matching keys, while inherited entries flow through
unchanged. This mirrors SPICE's ``.SUBCKT name pins... PARAMS:`` /
``Xinst pins... name PARAMS:`` semantics.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, ClassVar, Iterator, Mapping, Optional

from sycan import cas as cas
from sycan.mna import Component, NoiseSpec, StampContext, Value

if TYPE_CHECKING:
    from sycan.circuit import Circuit


def _sympify_params(raw: Mapping[str, Value]) -> dict[str, cas.Expr]:
    """Coerce every value in a ``params`` dict to a sympy expression."""
    return {str(k): cas.sympify(v) for k, v in raw.items()}


def _build_subs_map(params: Mapping[str, cas.Expr]) -> dict[cas.Symbol, cas.Expr]:
    """Turn a ``{name: value}`` map into ``{Symbol(name): value}``."""
    return {cas.Symbol(name): value for name, value in params.items()}


class _CollapsedGroup:
    """Synthetic placeholder for a collapsed :class:`SubCircuit` instance.

    Returned by :meth:`SubCircuit.expand_leaves` when the instance's
    full hierarchy path appears in ``collapse_paths``. Quacks like a
    :class:`Component` for downstream code that only needs the pin
    names and their parent-scope nodes — autodraw consumes it via a
    dedicated ``_describe`` handler that produces a generic multi-port
    placeholder _CompDesc.

    Attributes are populated dynamically from the resolved port map:
    ``getattr(self, <pin_name>) == <parent_node>`` for every pin, and
    ``ports`` is the ordered tuple of pin names. ``_group_path`` records
    the enclosing-scope chain (without ``self.name``) so the autodraw
    group-bounding-box logic still nests the placeholder inside any
    *outer* group that hasn't been collapsed.
    """

    def __init__(
        self,
        name: str,
        port_map: Mapping[str, str],
        group_path: tuple[str, ...],
    ) -> None:
        self.name = name
        self.port_map = dict(port_map)
        self.ports: tuple[str, ...] = tuple(self.port_map)
        for pin, node in self.port_map.items():
            setattr(self, pin, node)
        self._group_path = group_path

    def iter_node_names(self) -> Iterator[str]:
        yield from self.port_map.values()


def _substitute_leaf(
    leaf: Component, subs: Mapping[cas.Symbol, cas.Expr]
) -> Component:
    """Return a copy of ``leaf`` with parameter symbols replaced.

    Walks the leaf's dataclass fields and applies ``subs`` to any
    sympy expression, including expressions nested inside ``dict``
    fields (used by, e.g., behavioural-source operating-point maps).
    Returns ``leaf`` unchanged if no substitution actually fires.
    """
    if not subs:
        return leaf
    new_attrs: dict[str, object] = {}
    for f in dataclasses.fields(leaf):
        val = getattr(leaf, f.name, None)
        if isinstance(val, cas.Basic):
            new_val = val.xreplace(dict(subs))
            if new_val is not val and new_val != val:
                new_attrs[f.name] = new_val
        elif isinstance(val, dict):
            replaced: dict = {}
            changed = False
            for k, v in val.items():
                if isinstance(v, cas.Basic):
                    nv = v.xreplace(dict(subs))
                    if nv is not v and nv != v:
                        changed = True
                    replaced[k] = nv
                else:
                    replaced[k] = v
            if changed:
                new_attrs[f.name] = replaced
    if not new_attrs:
        return leaf
    return replace(leaf, **new_attrs)


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
    params: Mapping[str, Value] = field(default_factory=dict, kw_only=True)
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
        self.params = _sympify_params(self.params)
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

    def effective_params(
        self, inherited: Optional[Mapping[str, cas.Expr]] = None
    ) -> dict[str, cas.Expr]:
        """Merge inherited params with this instance's own params.

        Own params take precedence on key collision; inherited values
        flow through unchanged for keys this instance does not set.
        """
        merged: dict[str, cas.Expr] = dict(inherited) if inherited else {}
        merged.update(self.params)
        return merged

    def expand_leaves(
        self,
        name_prefix: Optional[str] = None,
        node_remap: Optional[dict[str, str]] = None,
        inherited_params: Optional[Mapping[str, cas.Expr]] = None,
        group_path: tuple[str, ...] = (),
        collapse_paths: Optional[frozenset[tuple[str, ...]]] = None,
    ) -> list:
        """Return the recursively-flattened leaf components.

        ``name_prefix`` and ``node_remap`` are used internally when this
        subcircuit is itself nested inside another; callers normally
        omit them. ``inherited_params`` carries the outer-scope parameter
        bindings that this instance's params can override. ``group_path``
        is the chain of enclosing :class:`SubCircuit` instance names —
        each leaf is tagged with a ``_group_path`` attribute extending
        this chain by ``self.name``, which downstream renderers
        (``autodraw``) use to keep grouped components clustered and to
        draw a bounding box around them. ``collapse_paths`` is the set
        of fully-qualified group paths (tuples like ``("X1", "U1")``)
        that should be replaced by a single :class:`_CollapsedGroup`
        placeholder instead of expanded — used by autodraw's
        ``collapse=`` parameter to hide implementation detail.
        """
        if name_prefix is None:
            name_prefix = self.name
        if node_remap is None:
            node_remap = dict(self.port_map)

        merged_params = self.effective_params(inherited_params)
        subs = _build_subs_map(merged_params)
        own_path = group_path + (self.name,)

        if collapse_paths and own_path in collapse_paths:
            # Stop descending: emit a single placeholder whose pins map
            # to the parent-scope nodes that ``node_remap`` already
            # carries. Tag it with the *enclosing* group path so any
            # outer group still draws a bounding box around it.
            resolved = {
                pin: node_remap.get(pin, f"{name_prefix}.{pin}")
                for pin in self.port_map
            }
            return [_CollapsedGroup(name=name_prefix,
                                    port_map=resolved,
                                    group_path=group_path)]

        def resolve(inner_node: str) -> str:
            if inner_node == "0":
                return "0"
            if inner_node in node_remap:
                return node_remap[inner_node]
            return f"{name_prefix}.{inner_node}"

        leaves: list = []
        for inner in self.body.components:
            if isinstance(inner, SubCircuit):
                nested_remap = {
                    pin: resolve(conn) for pin, conn in inner.port_map.items()
                }
                leaves.extend(
                    inner.expand_leaves(
                        name_prefix=f"{name_prefix}.{inner.name}",
                        node_remap=nested_remap,
                        inherited_params=merged_params,
                        group_path=own_path,
                        collapse_paths=collapse_paths,
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
                clone = replace(inner, **new_attrs)
                clone = _substitute_leaf(clone, subs)
                # Attach the group path post-replace (replace would not
                # carry custom attributes through). The setattr is safe
                # because dataclasses don't define __slots__ here.
                try:
                    object.__setattr__(clone, "_group_path", own_path)
                except (AttributeError, TypeError):
                    pass
                leaves.append(clone)
        return leaves
