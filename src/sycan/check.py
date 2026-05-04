"""Schematic / netlist electrical-rule checks.

The :func:`check_circuit` helper walks a :class:`~sycan.circuit.Circuit`
and surfaces structural problems that are easy to mis-spot in a
hand-written netlist:

* duplicate component names (ambiguous I() / aux references),
* nodes connected to only one component pin (dangling wires),
* circuits that never touch ground node ``"0"``,
* "islands" — node graphs disconnected from ground,
* components whose port nodes overlap (short between two pins of the
  same device).

It returns a :class:`ERCReport` so callers can either print the report
or assert :attr:`ERCReport.ok`.  The function is read-only: it never
mutates the input circuit.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sycan.circuit import Circuit


@dataclass
class ERCFinding:
    """One ERC issue.

    ``severity`` is ``"error"`` (definitely wrong) or ``"warning"``
    (suspicious, may be intentional). ``message`` is a one-line
    human-readable description.
    """

    severity: str
    code: str
    message: str


@dataclass
class ERCReport:
    """Aggregated ERC findings for a circuit."""

    findings: list[ERCFinding] = field(default_factory=list)

    @property
    def errors(self) -> list[ERCFinding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warnings(self) -> list[ERCFinding]:
        return [f for f in self.findings if f.severity == "warning"]

    @property
    def ok(self) -> bool:
        """``True`` when no error-level findings were recorded."""
        return not self.errors

    def add(self, severity: str, code: str, message: str) -> None:
        self.findings.append(ERCFinding(severity, code, message))

    def __str__(self) -> str:
        if not self.findings:
            return "ERC: clean."
        lines = [f"ERC: {len(self.errors)} error(s), {len(self.warnings)} warning(s)"]
        for f in self.findings:
            lines.append(f"  [{f.severity}:{f.code}] {f.message}")
        return "\n".join(lines)


def check_circuit(circuit: "Circuit") -> ERCReport:
    """Run structural checks and return a :class:`ERCReport`.

    The checks are intentionally cheap and netlist-only — no MNA
    assembly, no symbolic work — so :func:`check_circuit` is suitable
    for editor-side linting.
    """
    report = ERCReport()
    flat = circuit.flat_components()

    _check_duplicate_names(flat, report)
    _check_self_loops(flat, report)
    pin_counts = _count_pins(flat)
    _check_dangling_nodes(pin_counts, report)
    _check_ground_reference(circuit, pin_counts, report)
    _check_islands(flat, circuit, report)

    return report


def _check_duplicate_names(flat: list, report: ERCReport) -> None:
    seen: dict[str, int] = {}
    for c in flat:
        seen[c.name] = seen.get(c.name, 0) + 1
    for name, n in seen.items():
        if n > 1:
            report.add(
                "error",
                "DUPLICATE_NAME",
                f"component name {name!r} reused {n} times — "
                "I(<name>) references will be ambiguous",
            )


def _check_self_loops(flat: list, report: ERCReport) -> None:
    for c in flat:
        nodes = [getattr(c, attr) for attr in c.ports if getattr(c, attr, None) is not None]
        # A two-terminal device with both terminals on the same node is
        # a short — almost never intentional.
        if len(c.ports) == 2 and len(set(nodes)) == 1 and nodes:
            report.add(
                "warning",
                "PIN_SHORT",
                f"{c.name}: both terminals connected to node {nodes[0]!r} "
                "(self-short)",
            )


def _count_pins(flat: list) -> dict[str, int]:
    counts: dict[str, int] = {}
    for c in flat:
        for attr in c.ports:
            node = getattr(c, attr, None)
            if node is None:
                continue
            counts[node] = counts.get(node, 0) + 1
    return counts


def _check_dangling_nodes(pin_counts: dict[str, int], report: ERCReport) -> None:
    for node, n in pin_counts.items():
        if node == "0":
            continue
        if n < 2:
            report.add(
                "warning",
                "DANGLING_NODE",
                f"node {node!r} has only {n} pin(s) attached — "
                "likely floating",
            )


def _check_ground_reference(
    circuit: "Circuit", pin_counts: dict[str, int], report: ERCReport
) -> None:
    if "0" not in circuit._nodes:
        # Should be impossible — Circuit always seeds "0" — but guard
        # anyway.
        report.add("error", "NO_GROUND", "circuit has no ground node")
        return
    if pin_counts.get("0", 0) == 0:
        report.add(
            "error",
            "FLOATING_GROUND",
            "ground node '0' is not referenced by any component — "
            "MNA will produce a singular system",
        )


def _check_islands(flat: list, circuit: "Circuit", report: ERCReport) -> None:
    """Find connected components in the node graph and warn if any
    are disconnected from ground.
    """
    # Union-find over node names.
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    # Seed every registered node so isolated ones still appear.
    for n in circuit._nodes:
        find(n)
    for c in flat:
        node_list = [
            getattr(c, attr) for attr in c.ports if getattr(c, attr, None) is not None
        ]
        # Mutual-coupling style components have no port pins of their own;
        # skip them for connectivity purposes.
        if not node_list:
            continue
        first = node_list[0]
        for n in node_list[1:]:
            union(first, n)

    gnd = find("0")
    isolated: list[str] = []
    for n in circuit._nodes:
        if n == "0":
            continue
        if find(n) != gnd:
            isolated.append(n)
    if isolated:
        report.add(
            "warning",
            "ISLAND",
            f"{len(isolated)} node(s) disconnected from ground "
            f"(no DC return path): {sorted(isolated)!r}",
        )
