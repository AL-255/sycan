"""Auto-place a netlist as an SVG schematic.

Pipeline
--------
1.  *Graph build* — map each component onto a "spine" (its high-current
    path) and a list of "side" ports. The spine for transistors is
    Drain-Source / Collector-Emitter / Plate-Cathode (the carrier path),
    so vertical stacks of these devices come out as straight power-rail
    columns. Wire-shorts (SPICE ``W``) and explicit ``GND`` ties are
    folded into a union-find on the nets.
2.  *Branch finding* — greedily walk components from a top rail (VDD /
    VCC) to a bottom rail (VSS / VEE / GND / "0") via the spine. Each
    successful walk becomes a vertical column in the layout.
3.  *Placement* — branch columns are laid out left to right with the top
    rail and bottom rail as horizontal trunks. Components left over
    (feedback, coupling, controlled sources, ...) get their own
    floating columns.
4.  *Routing* — each remaining net (the side ports, plus rail crossings
    that didn't fold into a single branch) is routed with a Lee /
    Hadlock-style BFS on a coarse routing grid. Component bounding
    boxes are blocked cells, so wires never cross a component body.
    Cells already occupied by a wire incur a small penalty so later
    nets prefer fresh space, which keeps clutter down.
5.  *Emit SVG* — components are rendered as labelled boxes with port
    pins; wires are emitted as polylines.

Polarity-aware orientation: NMOS / NPN / triode / diode / V-source put
their canonical "top" terminal toward the higher rail; PMOS / PNP put
source / emitter toward the higher rail. The placement walker may flip
that orientation per-instance to follow the spine, in which case the
port labels swap with it.

The output is intentionally schematic — boxes and ports, no symbols —
so a downstream renderer can replace each ``<rect data-comp="...">``
with the actual device glyph.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Sequence, Union

from sycan.circuit import Circuit
from sycan.components.active import (
    BJT,
    Diode,
    NMOS_3T,
    NMOS_L1,
    NMOS_subthreshold,
    PMOS_3T,
    PMOS_L1,
    PMOS_subthreshold,
    Triode,
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
from sycan.components.rf import TLINE
from sycan.mna import Component


# ---------------------------------------------------------------------------
# Layout constants (px). Tweaking these here changes the visual density.
# ---------------------------------------------------------------------------
COL_W = 30
ROW_H = 100
BOX_W = 40
BOX_H = 40
PORT_LEN = 14
PAD = 70
RAIL_GAP = 20           # extra space between rail and first/last component
LABEL_FS = 11
PORT_FS = 9
STROKE = 1.0
GRID_PX = 10           # final-rendering routing grid resolution
SA_GRID_PX = 10         # cost-evaluation grid (must match the final grid so
                        # tight inter-component clearances don't read as
                        # unroutable in the cost when they actually route fine)
MIN_GAP = 30            # min edge-to-edge clearance between two components
                        # stacked in the same column
MIN_PITCH = BOX_H + MIN_GAP  # default center-to-center distance for two
                             # default-sized boxes; per-component with glyphs
                             # uses (h_a + h_b)/2 + MIN_GAP at evaluation time


_TOP_RAIL_DEFAULT = ("VDD", "VCC", "VPP")
_BOT_RAIL_DEFAULT = ("VSS", "VEE", "GND", "0")

# Fallback SA seeds tried in order when the rendered layout has wires
# laying on top of each other. The list is long enough to satisfy the
# default ``max_retries=5`` even if the caller's seed already appears
# in it. See :func:`autodraw` for the retry mechanism.
_RETRY_SEEDS = (1, 7, 17, 31, 42, 145, 199, 999, 2025, 31415)

# Cross-/same-net collinear overlap length (px) above which the
# autodraw retry loop discards a render and tries the next seed. A
# clean schematic produces zero — any non-trivial overlap means the
# router was forced to fuse two segments visually, so even a small
# threshold is enough.
_OVERLAP_TOL = 1.0


# ---------------------------------------------------------------------------
# Component → spine / side port mapping.
# ---------------------------------------------------------------------------
@dataclass
class _CompDesc:
    """Drawing-time view of a Component.

    ``spine_top`` / ``spine_bot`` are the *canonical* port names on the
    high-current path. ``flip`` flags that the instance was placed with
    its spine inverted (so ``spine_bot`` ends up at the top of the box).
    ``mirror`` swaps which physical side (left vs right of the box) each
    side port goes on; the SA layer flips this to shorten side-port
    routes across columns.

    ``bbox_w`` / ``bbox_h`` are the component's drawing dimensions in
    canvas units. They default to the global ``BOX_W`` / ``BOX_H`` and
    are overridden by the corresponding glyph's ``viewBox`` when a
    ``res/<kind>.svg`` exists. ``port_offsets`` maps each port name to
    its native ``(x, y)`` location relative to the box's top-left
    corner; ``flip`` (vertical mirror) and ``mirror`` (horizontal
    mirror) are applied at evaluation time.
    """

    component: Component
    label: str
    kind: str
    spine_top: str
    spine_bot: str
    side_ports: tuple[str, ...] = ()
    flip: bool = False
    mirror: bool = False
    rail_anchored: bool = False  # set by _build_branches; if True, _sa_optimize
                                 # leaves the spine flip alone (otherwise the
                                 # rail-side port would jump to the wrong end).
    bbox_w: float = 0.0  # populated by _apply_glyphs (defaults to BOX_W)
    bbox_h: float = 0.0  # populated by _apply_glyphs (defaults to BOX_H)
    port_offsets: dict[str, tuple[float, float]] = field(default_factory=dict)

    def port_net(self, port: str) -> str:
        return getattr(self.component, port)

    def top_port(self) -> str:
        return self.spine_bot if self.flip else self.spine_top

    def bot_port(self) -> str:
        return self.spine_top if self.flip else self.spine_bot

    def top_net(self) -> str:
        return self.port_net(self.top_port())

    def bot_net(self) -> str:
        return self.port_net(self.bot_port())


def _short(port: str) -> str:
    """One/two-letter glyph used at port pins."""
    return {
        "drain": "D",
        "gate": "G",
        "source": "S",
        "collector": "C",
        "base": "B",
        "emitter": "E",
        "plate": "P",
        "grid": "G",
        "cathode": "K",
        "anode": "A",
        "n_plus": "+",
        "n_minus": "-",
        "nc_plus": "c+",
        "nc_minus": "c-",
        "ctrl": "i",
        "node": "x",
        "n_in_p": "1+",
        "n_in_m": "1-",
        "n_out_p": "2+",
        "n_out_m": "2-",
    }.get(port, port[:2])


def _describe(c: Component) -> _CompDesc:
    """Return the spine + sides for any supported component type."""
    if isinstance(c, (NMOS_L1, NMOS_subthreshold, NMOS_3T)):
        return _CompDesc(c, c.name, "nmos",
                         spine_top="drain", spine_bot="source",
                         side_ports=("gate",))
    if isinstance(c, (PMOS_L1, PMOS_subthreshold, PMOS_3T)):
        return _CompDesc(c, c.name, "pmos",
                         spine_top="source", spine_bot="drain",
                         side_ports=("gate",))
    if isinstance(c, BJT):
        if c.polarity == "NPN":
            return _CompDesc(c, c.name, "npn",
                             spine_top="collector", spine_bot="emitter",
                             side_ports=("base",))
        return _CompDesc(c, c.name, "pnp",
                         spine_top="emitter", spine_bot="collector",
                         side_ports=("base",))
    if isinstance(c, Triode):
        return _CompDesc(c, c.name, "triode",
                         spine_top="plate", spine_bot="cathode",
                         side_ports=("grid",))
    if isinstance(c, Diode):
        return _CompDesc(c, c.name, "diode",
                         spine_top="anode", spine_bot="cathode")
    if isinstance(c, VoltageSource):
        return _CompDesc(c, c.name, "vsrc",
                         spine_top="n_plus", spine_bot="n_minus")
    if isinstance(c, CurrentSource):
        return _CompDesc(c, c.name, "isrc",
                         spine_top="n_plus", spine_bot="n_minus")
    if isinstance(c, Resistor):
        return _CompDesc(c, c.name, "res",
                         spine_top="n_plus", spine_bot="n_minus")
    if isinstance(c, Inductor):
        return _CompDesc(c, c.name, "ind",
                         spine_top="n_plus", spine_bot="n_minus")
    if isinstance(c, Capacitor):
        return _CompDesc(c, c.name, "cap",
                         spine_top="n_plus", spine_bot="n_minus")
    if isinstance(c, TLINE):
        return _CompDesc(c, c.name, "tline",
                         spine_top="n_in_p", spine_bot="n_out_p",
                         side_ports=("n_in_m", "n_out_m"))
    if isinstance(c, (VCVS, VCCS)):
        return _CompDesc(c, c.name, "ccsrc",
                         spine_top="n_plus", spine_bot="n_minus",
                         side_ports=("nc_plus", "nc_minus"))
    if isinstance(c, (CCCS, CCVS)):
        return _CompDesc(c, c.name, "ccsrc",
                         spine_top="n_plus", spine_bot="n_minus",
                         side_ports=("ctrl",))
    if isinstance(c, Port):
        return _CompDesc(c, c.name, "port",
                         spine_top="n_plus", spine_bot="n_minus")
    if isinstance(c, GND):
        return _CompDesc(c, c.name, "gnd",
                         spine_top="node", spine_bot="node")
    raise TypeError(f"autodraw: unsupported component {type(c).__name__}")


# ---------------------------------------------------------------------------
# Net union-find for wire-shorts and explicit GNDs.
# ---------------------------------------------------------------------------
class _UF:
    def __init__(self) -> None:
        self.p: dict[str, str] = {}

    def find(self, a: str) -> str:
        self.p.setdefault(a, a)
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        # Prefer to keep the more "canonical"-looking name as the root:
        # "0" / GND-rail names beat plain identifiers.
        if _is_ground(ra):
            self.p[rb] = ra
        elif _is_ground(rb):
            self.p[ra] = rb
        else:
            self.p[rb] = ra


def _is_ground(net: str) -> bool:
    return net == "0" or net.upper() == "GND"


# ---------------------------------------------------------------------------
# Branch (vertical column) finder.
# ---------------------------------------------------------------------------
@dataclass
class _Branch:
    descs: list[_CompDesc] = field(default_factory=list)


def _classify(net: str, top_set: set[str], bot_set: set[str]) -> str:
    if net in top_set:
        return "top"
    if net in bot_set:
        return "bot"
    return "mid"


def _build_branches(
    descs: Sequence[_CompDesc],
    uf: _UF,
    top_set: set[str],
    bot_set: set[str],
) -> tuple[list[_Branch], dict[str, list[tuple[_CompDesc, str]]]]:
    """Group components into vertical columns.

    Strategy (run in this order):

    A. Walk down from every component touching the top rail through
       *non-junction* spine nets only. A "junction" is a net with more
       than two spine endpoints — extending through one would conflate
       parallel stacks into a single column.
    B. Walk up from every component touching the bot rail, with the
       same non-junction rule. Skip components whose walk-up direction
       hits a junction immediately — let phase C decide what to do
       with them.
    C. Junction extension. A branch ending at a mid junction can be
       extended downward through an unused, rail-bound candidate when
       *no other branch* also ends at that junction (otherwise the
       parallel branches would compete and the junction is best left
       as a shared node, e.g., a diff-pair tail). Symmetrically extend
       branches starting at a junction upward.
    D. Anything still unused becomes a one-component column.
    """

    used: set[int] = set()  # by id(_CompDesc)
    branches: list[_Branch] = []

    # Convenience: descs whose spine port lies on a given canonical net.
    spine_index: dict[str, list[tuple[_CompDesc, str]]] = {}
    for d in descs:
        seen: set[str] = set()
        for p in (d.spine_top, d.spine_bot):
            if p in seen:
                continue
            seen.add(p)
            n = uf.find(d.port_net(p))
            spine_index.setdefault(n, []).append((d, p))

    def kind(net: str) -> str:
        return _classify(net, top_set, bot_set)

    def distinct_at(net: str) -> int:
        return len({id(cand) for cand, _ in spine_index.get(net, ())})

    def step(cur_net: str, going: str
             ) -> Optional[tuple[_CompDesc, str, str]]:
        """Step toward ``going`` (``'top'`` or ``'bot'``).

        Reject candidates whose other spine endpoint sits on the rail we
        are walking *away* from (that would be a U-turn).
        """
        avoid = "top" if going == "bot" else "bot"
        for cand, port in spine_index.get(cur_net, ()):
            if id(cand) in used or cand.kind == "port":
                continue
            other = cand.spine_bot if port == cand.spine_top else cand.spine_top
            other_net = uf.find(cand.port_net(other))
            if kind(other_net) == avoid:
                continue
            return cand, port, other_net
        return None

    # ---- Phase A: walk down from top rail ----
    for d in descs:
        if id(d) in used:
            continue
        top_at_top = kind(uf.find(d.port_net(d.spine_top))) == "top"
        bot_at_top = kind(uf.find(d.port_net(d.spine_bot))) == "top"
        if not (top_at_top or bot_at_top):
            continue
        d.flip = bot_at_top
        d.rail_anchored = True

        branch = _Branch([d])
        used.add(id(d))
        cur_net = uf.find(d.bot_net())

        while kind(cur_net) == "mid" and distinct_at(cur_net) <= 2:
            r = step(cur_net, going="bot")
            if r is None:
                break
            cand, port, _other_net = r
            cand.flip = (port == cand.spine_bot)
            branch.descs.append(cand)
            used.add(id(cand))
            cur_net = uf.find(cand.bot_net())

        if kind(cur_net) == "bot" and branch.descs:
            branch.descs[-1].rail_anchored = True
        branches.append(branch)

    # ---- Phase B: walk up from bot rail (non-junction take-off only) ----
    for d in descs:
        if id(d) in used:
            continue
        bot_at_bot = kind(uf.find(d.port_net(d.spine_bot))) == "bot"
        top_at_bot = kind(uf.find(d.port_net(d.spine_top))) == "bot"
        if not (bot_at_bot or top_at_bot):
            continue
        d.flip = top_at_bot
        d.rail_anchored = True

        # If this component's walk-up direction is already a junction,
        # don't claim it here — phase C may want to attach it to an
        # existing top branch instead.
        first_above = uf.find(d.top_net())
        if kind(first_above) == "mid" and distinct_at(first_above) > 2:
            continue

        branch = _Branch([d])
        used.add(id(d))
        cur_net = first_above

        while kind(cur_net) == "mid" and distinct_at(cur_net) <= 2:
            r = step(cur_net, going="top")
            if r is None:
                break
            cand, port, _other_net = r
            cand.flip = (port == cand.spine_top)
            branch.descs.insert(0, cand)
            used.add(id(cand))
            cur_net = uf.find(cand.top_net())

        if kind(cur_net) == "top" and branch.descs:
            branch.descs[0].rail_anchored = True
        branches.append(branch)

    # ---- Phase C: junction extension ----
    def _try_extend(
        branch: _Branch,
        net: str,
        going: str,            # 'bot' = extend downward, 'top' = upward
    ) -> bool:
        target = going  # rail kind we want the candidate's other end on
        for cand, port in spine_index.get(net, ()):
            if id(cand) in used or cand.kind == "port":
                continue
            other = cand.spine_bot if port == cand.spine_top else cand.spine_top
            other_net = uf.find(cand.port_net(other))
            if kind(other_net) != target:
                continue
            if going == "bot":
                cand.flip = (port == cand.spine_bot)
                branch.descs.append(cand)
            else:
                cand.flip = (port == cand.spine_top)
                branch.descs.insert(0, cand)
            cand.rail_anchored = True
            used.add(id(cand))
            return True
        return False

    # Down-extensions: branches ending at a mid junction.
    ending_at: dict[str, list[_Branch]] = {}
    for b in branches:
        if not b.descs:
            continue
        last_net = uf.find(b.descs[-1].bot_net())
        if kind(last_net) == "mid" and distinct_at(last_net) > 2:
            ending_at.setdefault(last_net, []).append(b)
    for net, bs in ending_at.items():
        if len(bs) == 1:
            _try_extend(bs[0], net, going="bot")

    # Up-extensions: branches starting at a mid junction.
    starting_at: dict[str, list[_Branch]] = {}
    for b in branches:
        if not b.descs:
            continue
        first_net = uf.find(b.descs[0].top_net())
        if kind(first_net) == "mid" and distinct_at(first_net) > 2:
            starting_at.setdefault(first_net, []).append(b)
    for net, bs in starting_at.items():
        if len(bs) == 1:
            _try_extend(bs[0], net, going="top")

    # ---- Phase D: leftovers ----
    for d in descs:
        if id(d) in used:
            continue
        branches.append(_Branch([d]))
        used.add(id(d))

    return branches, spine_index


# ---------------------------------------------------------------------------
# Placement.
# ---------------------------------------------------------------------------
@dataclass
class _Placed:
    desc: _CompDesc
    cx: float          # box center x
    cy: float          # box center y
    pin_pos: dict[str, tuple[float, float]] = field(default_factory=dict)
    pin_side: dict[str, str] = field(default_factory=dict)  # "top"/"bot"/"left"/"right"


def _logical_chain_length(
    branches: Sequence[_Branch],
    uf: _UF,
    spine_index: dict[str, list[tuple[_CompDesc, str]]],
) -> int:
    """Longest "logical" stack through any spine junction.

    A junction net is a mid net with more than two spine endpoints
    (e.g. the diff-pair tail). At such a net the layout must
    accommodate the longest branch ending there *plus* the longest
    branch starting there, so their respective rail-side spine pins
    can sit at the same y and the trunk through the junction stays a
    single horizontal line. If we only sized the canvas for the
    longest *physical* column we'd often not have enough vertical
    room to do that, and the optimizer would be forced to fold the
    trunk into a U-shape.

    Returns the length of the longest such logical chain (in
    component count); never less than the longest physical branch.
    """
    if not branches:
        return 1

    above: dict[str, list[int]] = {}  # branches whose bottom hits this net
    below: dict[str, list[int]] = {}  # branches whose top hits this net

    def is_junction(net: str) -> bool:
        return len({id(c) for c, _ in spine_index.get(net, ())}) > 2

    for b in branches:
        if not b.descs:
            continue
        last_net = uf.find(b.descs[-1].bot_net())
        if is_junction(last_net):
            above.setdefault(last_net, []).append(len(b.descs))
        first_net = uf.find(b.descs[0].top_net())
        if is_junction(first_net):
            below.setdefault(first_net, []).append(len(b.descs))

    longest = max(len(b.descs) for b in branches if b.descs)
    for net in set(above) | set(below):
        a = max(above.get(net, [0]))
        b = max(below.get(net, [0]))
        longest = max(longest, a + b)
    return longest


def _column_widths(branches: Sequence[_Branch]) -> list[float]:
    """Per-branch drawing width in canvas units.

    Picks the widest component in the branch and adds a small gutter
    so wires can route along the column boundary. Each width is
    rounded up to a multiple of ``2 * GRID_PX`` so that the column
    centres themselves end up on the routing grid (otherwise different
    anchor offsets in the same column round through banker's rules to
    inconsistent cells).
    """
    import math

    step = 2 * GRID_PX
    out: list[float] = []
    for branch in branches:
        if not branch.descs:
            out.append(float(COL_W))
        else:
            widest = max(d.bbox_w for d in branch.descs)
            w = max(float(COL_W), widest + 56.0)
            w = math.ceil(w / step) * step
            out.append(w)
    return out


def _column_centers(
    branches: Sequence[_Branch],
    col_order: Sequence[int],
    col_widths: Sequence[float],
    x0: float,
) -> tuple[dict[int, float], float]:
    """Map ``branch_idx`` → column-center x; also return total canvas width."""
    centers: dict[int, float] = {}
    cur = x0
    for b_idx in col_order:
        w = col_widths[b_idx]
        centers[b_idx] = cur + w / 2.0
        cur += w
    canvas_w = cur + x0
    return centers, canvas_w


def _default_y_positions(
    branches: Sequence[_Branch],
    rail_top_y: float,
    rail_bot_y: float,
    canonical_top: Optional[set[str]] = None,
    canonical_bot: Optional[set[str]] = None,
    uf: Optional[_UF] = None,
) -> dict[int, float]:
    """Place each branch's components inside the rails, leaving room
    for pin stubs and inter-box gaps.

    Branches whose top spine pin sits on a top rail get packed against
    the top of the available span; bot-rail-anchored ones get packed
    against the bottom; both-rail-anchored ones get stretched. This
    matters whenever the canvas is taller than the longest physical
    column (e.g., diff-pair where the canvas is sized for a 3-deep
    logical chain): without rail-side packing, the SA optimizer would
    have to undo the centred default before the real wirelength
    starts dropping.
    """
    edge_gap = float(PORT_LEN)         # pin-stub room above first / below last box
    inter_gap = max(MIN_GAP, 2 * PORT_LEN)  # box-edge to box-edge in a column
    available_top = rail_top_y + RAIL_GAP + edge_gap
    available_bot = rail_bot_y - RAIL_GAP - edge_gap

    def at_rail(net_set, net):
        if net_set is None or uf is None:
            return False
        return uf.find(net) in net_set

    y_pos: dict[int, float] = {}
    for branch in branches:
        descs = branch.descs
        n = len(descs)
        if n == 0:
            continue
        first_d, last_d = descs[0], descs[-1]
        top_anchored = (
            first_d.rail_anchored
            and at_rail(canonical_top, first_d.port_net(first_d.top_port()))
        )
        bot_anchored = (
            last_d.rail_anchored
            and at_rail(canonical_bot, last_d.port_net(last_d.bot_port()))
        )

        total_box_h = sum(d.bbox_h for d in descs)
        required_h = total_box_h + max(0, n - 1) * inter_gap
        avail_h = max(0.0, available_bot - available_top)
        slack = max(0.0, avail_h - required_h)

        if top_anchored and bot_anchored:
            extra = slack / (n + 1) if n >= 1 else 0.0
            cur_top = available_top + extra
            for d in descs:
                y_pos[id(d)] = cur_top + d.bbox_h / 2.0
                cur_top += d.bbox_h + inter_gap + extra
        elif top_anchored:
            cur_top = available_top
            for d in descs:
                y_pos[id(d)] = cur_top + d.bbox_h / 2.0
                cur_top += d.bbox_h + inter_gap
        elif bot_anchored:
            cur_bot = available_bot
            for d in reversed(descs):
                y_pos[id(d)] = cur_bot - d.bbox_h / 2.0
                cur_bot -= d.bbox_h + inter_gap
        else:
            cur_top = available_top + slack / 2.0
            for d in descs:
                y_pos[id(d)] = cur_top + d.bbox_h / 2.0
                cur_top += d.bbox_h + inter_gap
    return y_pos


def _layout(
    branches: Sequence[_Branch],
    col_order: Sequence[int],
    col_widths: Sequence[float],
    col_centers: dict[int, float],
    y_pos: dict[int, float],
) -> list[_Placed]:
    """Build ``_Placed`` records for the given column order and y."""
    placed: list[_Placed] = []
    branch_index = {id(b): i for i, b in enumerate(branches)}

    for branch in branches:
        b_idx = branch_index[id(branch)]
        cx_raw = col_centers[b_idx]
        bw_col = col_widths[b_idx]  # noqa: F841 (kept for future extension)
        for d in branch.descs:
            cy_raw = y_pos[id(d)]
            bw, bh = d.bbox_w, d.bbox_h
            # Anchor the box so the *spine-top* pin lands at the column
            # centre, regardless of bbox width. With mixed-width
            # glyphs in the same column (e.g. a 70-wide resistor over
            # a 60-wide NMOS), aligning bbox centres would leave their
            # spine pins offset by (bw_a - bw_b) / 2; aligning the
            # spine pin instead keeps the vertical wire perfectly
            # straight. The box itself can sit asymmetrically around
            # the column — that is the user's responsibility to
            # design glyphs for.
            anchor_port = d.spine_top if not d.flip else d.spine_bot
            anchor_ox, _ = d.port_offsets.get(anchor_port, (bw / 2.0, 0))
            if d.mirror:
                anchor_ox = bw - anchor_ox
            box_left = round((cx_raw - anchor_ox) / GRID_PX) * GRID_PX
            box_top = round((cy_raw - bh / 2.0) / GRID_PX) * GRID_PX
            cx = box_left + bw / 2.0
            cy = box_top + bh / 2.0
            p = _Placed(d, cx, cy)

            # Pin positions: native offsets, with flip/mirror applied.
            # All offsets are GRID_PX multiples (the lint enforces it),
            # and box_left is grid-aligned, so pins land on grid too.
            for port, (ox, oy) in d.port_offsets.items():
                if d.mirror:
                    ox = bw - ox
                if d.flip:
                    oy = bh - oy
                px = box_left + ox
                py = box_top + oy
                p.pin_pos[port] = (px, py)
                if py <= cy - bh / 2.0 + 0.5:
                    p.pin_side[port] = "top"
                elif py >= cy + bh / 2.0 - 0.5:
                    p.pin_side[port] = "bot"
                elif px <= cx - bw / 2.0 + 0.5:
                    p.pin_side[port] = "left"
                else:
                    p.pin_side[port] = "right"
            placed.append(p)
    return placed


# ---------------------------------------------------------------------------
# Net collection — what wires need to be drawn.
# ---------------------------------------------------------------------------
def _collect_nets(
    placed: Sequence[_Placed],
    uf: _UF,
) -> dict[str, list[tuple[_Placed, str]]]:
    """Map canonical-net → list of (placed, port_name) endpoints."""
    nets: dict[str, list[tuple[_Placed, str]]] = {}
    for p in placed:
        for port in p.pin_pos:
            net = uf.find(p.desc.port_net(port))
            nets.setdefault(net, []).append((p, port))
    return nets


# ---------------------------------------------------------------------------
# Routing on a coarse grid (Lee BFS, with congestion penalty).
# ---------------------------------------------------------------------------
class _RouteGrid:
    def __init__(self, w: int, h: int) -> None:
        self.w = w
        self.h = h
        self.blocked = [[False] * h for _ in range(w)]
        self.used = [[0] * h for _ in range(w)]
        self.clearance = [[0] * h for _ in range(w)]  # filled by finalize()
        self.allow_pin: set[tuple[int, int]] = set()

    def block_rect(self, x0: int, y0: int, x1: int, y1: int) -> None:
        for x in range(max(0, x0), min(self.w, x1)):
            for y in range(max(0, y0), min(self.h, y1)):
                self.blocked[x][y] = True

    def finalize(self) -> None:
        """Compute a per-cell *clearance penalty* — distance band to
        the nearest blocked cell, capped at 2. Cells adjacent to a
        component get a higher penalty so the BFS prefers routes
        through open space, which makes L- and S-shaped traces curve
        *away* from components instead of running tight along their
        edges.
        """
        # Multi-source BFS from blocked cells.
        from collections import deque
        dist = [[3] * self.h for _ in range(self.w)]
        q: deque[tuple[int, int]] = deque()
        for x in range(self.w):
            for y in range(self.h):
                if self.blocked[x][y]:
                    dist[x][y] = 0
                    q.append((x, y))
        while q:
            x, y = q.popleft()
            d = dist[x][y]
            if d >= 2:
                continue
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                if not (0 <= nx < self.w and 0 <= ny < self.h):
                    continue
                if dist[nx][ny] > d + 1:
                    dist[nx][ny] = d + 1
                    q.append((nx, ny))
        # Distance-band penalty: cells adjacent to a block cost the
        # most, cells two away cost a bit less, and cells three or
        # more away pay nothing. The BFS aggregates this across the
        # whole path, so wires naturally curve away from component
        # edges into open space.
        for x in range(self.w):
            for y in range(self.h):
                if self.blocked[x][y]:
                    self.clearance[x][y] = 0
                    continue
                d = dist[x][y]
                if d == 1:
                    self.clearance[x][y] = 2
                elif d == 2:
                    self.clearance[x][y] = 1
                else:
                    self.clearance[x][y] = 0

    def lee(self, src: tuple[int, int], dst_set: set[tuple[int, int]]
            ) -> Optional[list[tuple[int, int]]]:
        """Shortest-path Dijkstra. Returns cell path."""
        if not dst_set:
            return None
        if src in dst_set:
            return [src]

        INF = 10 ** 9
        dist = [[INF] * self.h for _ in range(self.w)]
        prev: dict[tuple[int, int], tuple[int, int]] = {}
        sx, sy = src
        if not (0 <= sx < self.w and 0 <= sy < self.h):
            return None
        dist[sx][sy] = 0

        from heapq import heappush, heappop
        heap: list[tuple[int, int, int]] = [(0, sx, sy)]

        target_hit: Optional[tuple[int, int]] = None
        while heap:
            cost, x, y = heappop(heap)
            if cost > dist[x][y]:
                continue
            if (x, y) in dst_set:
                target_hit = (x, y)
                break
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                if not (0 <= nx < self.w and 0 <= ny < self.h):
                    continue
                if self.blocked[nx][ny] and (nx, ny) not in dst_set \
                        and (nx, ny) not in self.allow_pin:
                    continue
                # Base step + congestion penalty + clearance penalty
                # (cells next to a component cost more; the L-shape
                # elbow that lives in open space then wins on ties).
                step = 1 + 4 * self.used[nx][ny] + self.clearance[nx][ny]
                # Penalize turns slightly to prefer straight wires.
                if (x, y) in prev:
                    px, py = prev[(x, y)]
                    if (x - px, y - py) != (dx, dy):
                        step += 2
                ncost = cost + step
                if ncost < dist[nx][ny]:
                    dist[nx][ny] = ncost
                    prev[(nx, ny)] = (x, y)
                    heappush(heap, (ncost, nx, ny))

        if target_hit is None:
            return None
        path = [target_hit]
        while path[-1] != src:
            path.append(prev[path[-1]])
        path.reverse()
        return path

    def mark_used(self, path: Iterable[tuple[int, int]]) -> None:
        for x, y in path:
            self.used[x][y] += 1


def _find_solder_dots(
    routed_polylines: Sequence[tuple[str, list[tuple[float, float]]]],
    tol: float = 0.6,
) -> list[tuple[float, float]]:
    """Locate Steiner T-junctions where solder dots should be drawn.

    A point gets a dot when 3 or more wire-rays of the *same net*
    meet there. A "ray" is the half-line going from the candidate
    point along a routed segment. Counting:

    * a polyline endpoint at the point contributes 1 ray (the
      direction of that segment);
    * a polyline corner at the point contributes 2 rays (one per
      incident segment);
    * a polyline segment passing straight *through* the point
      contributes 2 rays.

    Two rays = a normal L-bend or a clean pin meeting; three or more
    rays = a junction needing a connection dot.
    """
    by_net: dict[str, list[list[tuple[float, float]]]] = {}
    for cls, pts in routed_polylines:
        if not cls.startswith("net-") or len(pts) < 2:
            continue
        net_key = cls[len("net-"):]
        by_net.setdefault(net_key, []).append(pts)

    def _close(a: tuple[float, float], b: tuple[float, float]) -> bool:
        return abs(a[0] - b[0]) < tol and abs(a[1] - b[1]) < tol

    dots: list[tuple[float, float]] = []
    seen: set[tuple[float, float]] = set()
    for net, polys in by_net.items():
        # Candidate junction points: every corner used in any
        # polyline of this net.
        candidates: list[tuple[float, float]] = []
        for poly in polys:
            for p in poly:
                if not any(_close(p, c) for c in candidates):
                    candidates.append(p)

        for cand in candidates:
            cx, cy = cand
            rays = 0
            for poly in polys:
                for i in range(len(poly) - 1):
                    a = poly[i]
                    b = poly[i + 1]
                    on_a = _close(a, cand)
                    on_b = _close(b, cand)
                    if on_a and on_b:
                        # Zero-length segment — skip.
                        continue
                    if on_a or on_b:
                        rays += 1
                        continue
                    # Strictly interior?
                    if abs(a[0] - b[0]) < tol:
                        # vertical
                        if abs(cx - a[0]) < tol and \
                           min(a[1], b[1]) + tol < cy < max(a[1], b[1]) - tol:
                            rays += 2
                    elif abs(a[1] - b[1]) < tol:
                        # horizontal
                        if abs(cy - a[1]) < tol and \
                           min(a[0], b[0]) + tol < cx < max(a[0], b[0]) - tol:
                            rays += 2
            if rays >= 3:
                key = (round(cand[0], 1), round(cand[1], 1))
                if key not in seen:
                    seen.add(key)
                    dots.append(cand)
    return dots


def _segment_overlap_length(
    polylines: Sequence[tuple[str, Sequence[tuple[float, float]]]],
    tol: float = 0.5,
) -> float:
    """Total length over which two distinct wire segments lie collinear
    on top of each other.

    A clean schematic has zero overlap: same-net wires meet at junction
    points (counted as a solder dot, not an overlap), and different-net
    wires either cross at a point or stay apart. When the router is
    forced to lay multiple segments along the same line — typically
    because a column placement leaves no clearance for a second trunk —
    those segments visually fuse into a single line that misrepresents
    the netlist. The :func:`autodraw` retry loop uses this length as
    the trigger.

    Both same-net and cross-net overlaps count: same-net overlap means
    a Steiner branch was re-routed over a trunk it should have tapped
    via a junction, which is just as visually confusing.
    """
    horiz: dict[float, list[tuple[float, float, str]]] = {}
    vert: dict[float, list[tuple[float, float, str]]] = {}
    for cls, pts in polylines:
        net_key = cls
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            if abs(y0 - y1) < tol and abs(x0 - x1) >= tol:
                key = round((y0 + y1) / 2.0, 1)
                horiz.setdefault(key, []).append(
                    (min(x0, x1), max(x0, x1), net_key)
                )
            elif abs(x0 - x1) < tol and abs(y0 - y1) >= tol:
                key = round((x0 + x1) / 2.0, 1)
                vert.setdefault(key, []).append(
                    (min(y0, y1), max(y0, y1), net_key)
                )

    total = 0.0
    for bins in (horiz, vert):
        for segs in bins.values():
            for i in range(len(segs)):
                a0, a1, _na = segs[i]
                for j in range(i + 1, len(segs)):
                    b0, b1, _nb = segs[j]
                    lo = max(a0, b0)
                    hi = min(a1, b1)
                    if hi - lo > tol:
                        total += hi - lo
    return total


def _compact_blanks(
    placed: list,
    polylines: list,
    solder_dots: list,
    canvas_w: float,
    min_gap: float = GRID_PX,
) -> float:
    """Shrink x-ranges that hold no vertical layout content.

    A "blank" is an x-range where neither a component bbox nor a
    vertical or diagonal wire segment lives — only horizontal spans
    pass through. Each blank wider than ``min_gap`` is collapsed to
    ``min_gap``, and everything to the right of it slides left by the
    deficit. Horizontal segments that bridged the blank shorten
    automatically: their endpoints sit in occupied ranges and shift
    independently. Diagonal segments (the cross-coupled X) stay
    intact — both endpoints live inside the same merged occupied
    range, so they take the same shift and the slope is preserved.

    Mutates ``placed`` (cx, pin_pos), ``polylines`` (point lists), and
    ``solder_dots`` in place. Returns the new canvas width.
    """
    if not placed and not polylines:
        return canvas_w

    occupied: list[tuple[float, float]] = []
    for p in placed:
        bw = p.desc.bbox_w
        occupied.append((p.cx - bw / 2.0, p.cx + bw / 2.0))
    for cls, pts in polylines:
        if cls.startswith("rail-"):
            continue
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            if abs(y0 - y1) < 1e-6:
                continue
            occupied.append((min(x0, x1), max(x0, x1)))

    if not occupied:
        return canvas_w

    occupied.sort()
    merged: list[list[float]] = []
    for lo, hi in occupied:
        if merged and lo <= merged[-1][1] + 1e-6:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])

    shifts: list[tuple[float, float]] = []
    cum = 0.0
    for i in range(1, len(merged)):
        gap = merged[i][0] - merged[i - 1][1]
        if gap > min_gap + 1e-6:
            cum += gap - min_gap
            shifts.append((merged[i][0], cum))

    if not shifts:
        return canvas_w

    def shift_x(x: float) -> float:
        s = 0.0
        for thr, c in shifts:
            if x >= thr - 1e-6:
                s = c
            else:
                break
        return x - s

    for p in placed:
        p.cx = shift_x(p.cx)
        for port, (px, py) in list(p.pin_pos.items()):
            p.pin_pos[port] = (shift_x(px), py)

    for i, (cls, pts) in enumerate(polylines):
        polylines[i] = (cls, [(shift_x(x), y) for x, y in pts])

    solder_dots[:] = [(shift_x(x), y) for x, y in solder_dots]

    return canvas_w - shifts[-1][1]


def _polyline_from_path(path: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    """Compress a cell-path into corner-only points."""
    if len(path) < 2:
        return list(path)
    out = [path[0]]
    for i in range(1, len(path) - 1):
        px, py = path[i - 1]
        x, y = path[i]
        nx, ny = path[i + 1]
        if (x - px, y - py) != (nx - x, ny - y):
            out.append((x, y))
    out.append(path[-1])
    return out


# ---------------------------------------------------------------------------
# Layout optimization (simulated annealing on real grid wirelength).
#
# The SA's inner cost is the sum of *actually-routable* shortest grid paths
# for every multi-terminal net (Steiner-style: grow a tree by repeatedly
# attaching the closest remaining terminal via BFS), plus a small penalty
# for cells used by more than one net (a cheap proxy for crossings/
# congestion). This is more expensive than HPWL but gives much tighter
# layouts because it accounts for blocked component bodies.
# ---------------------------------------------------------------------------
def _build_pin_recipe(
    descs: Sequence[_CompDesc],
) -> dict[int, tuple[int, list[tuple[str, str, float, float]]]]:
    """Precompute per-component port offsets.

    Returns ``{id(d): (branch_idx, port_recipes)}`` where each recipe is
    ``(port, native_side, dx_off, dy_off)`` with the offsets relative to
    the component's center. The native side is what the port lands on
    with ``mirror=False`` and ``flip=False``; SA toggles those bits at
    eval time without rebuilding the recipe.
    """
    out: dict[int, tuple[int, list[tuple[str, str, float, float]]]] = {}
    return out  # filled in elsewhere; computed inline below


def _pin_positions_for_state(
    descs: Sequence[_CompDesc],
    branch_of: dict[int, int],
    col_order: Sequence[int],
    col_centers: dict[int, float],
    y_pos: dict[int, float],
    mirrors: dict[int, bool],
    flips: dict[int, bool],
) -> tuple[dict[tuple[int, str], tuple[float, float]],
           list[tuple[float, float, float, float]]]:
    """Compute pin ``(x, y)`` for every ``(id(component), port)`` pair,
    plus the list of component bounding-box rectangles
    ``(x0, y0, x1, y1)``.

    Each component carries its own ``bbox_w`` / ``bbox_h`` (set up by
    :func:`_apply_glyphs` based on the loaded glyph or the default
    rect), so glyphs of arbitrary sizes work transparently.
    """
    pins: dict[tuple[int, str], tuple[float, float]] = {}
    boxes: list[tuple[float, float, float, float]] = []
    for d in descs:
        b_idx = branch_of[id(d)]
        cx_raw = col_centers[b_idx]
        cy_raw = y_pos[id(d)]
        bw, bh = d.bbox_w, d.bbox_h
        flip = flips.get(id(d), d.flip)
        mirror = mirrors.get(id(d), d.mirror)
        # Match _layout: anchor box on the spine-top pin so its canvas
        # x equals the column centre even when bbox widths vary
        # between rows in the same column.
        anchor_port = d.spine_top if not flip else d.spine_bot
        anchor_ox, _ = d.port_offsets.get(anchor_port, (bw / 2.0, 0))
        if mirror:
            anchor_ox = bw - anchor_ox
        box_left = round((cx_raw - anchor_ox) / GRID_PX) * GRID_PX
        box_top = round((cy_raw - bh / 2.0) / GRID_PX) * GRID_PX
        boxes.append((box_left, box_top, box_left + bw, box_top + bh))

        for port, (ox, oy) in d.port_offsets.items():
            if mirror:
                ox = bw - ox
            if flip:
                oy = bh - oy
            pins[(id(d), port)] = (box_left + ox, box_top + oy)
    return pins, boxes


def _build_routable_nets(
    descs: Sequence[_CompDesc],
    uf: _UF,
    canonical_top: set[str],
    canonical_bot: set[str],
) -> tuple[dict[str, list[tuple[int, str]]],
           dict[str, list[tuple[int, str]]]]:
    """Split nets into *mid* (need full BFS) and *rail* (just stubs)."""
    raw: dict[str, list[tuple[int, str]]] = {}
    for d in descs:
        for port in (d.spine_top, d.spine_bot, *d.side_ports):
            net = uf.find(d.port_net(port))
            entry = (id(d), port)
            slot = raw.setdefault(net, [])
            if entry not in slot:
                slot.append(entry)
    mid: dict[str, list[tuple[int, str]]] = {}
    rail: dict[str, list[tuple[int, str]]] = {}
    for k, v in raw.items():
        if k in canonical_top or k in canonical_bot:
            rail[k] = v
        elif len(v) >= 2:
            mid[k] = v
    return mid, rail


def _route_total_wirelength(
    pins: dict[tuple[int, str], tuple[float, float]],
    boxes: Sequence[tuple[float, float, float, float]],
    mid_nets: dict[str, list[tuple[int, str]]],
    rail_nets: dict[str, list[tuple[int, str]]],
    canvas_w: float,
    canvas_h: float,
    rail_top_y: float,
    rail_bot_y: float,
    canonical_top: set[str],
    grid_px: int = SA_GRID_PX,
    crossing_weight: float = 60.0,
    unroutable_penalty: float = 4000.0,
    clearance: bool = False,
    cross_coupled_xnets: Optional[Sequence[tuple[str, str, int, int]]] = None,
) -> float:
    """Compute the actual rectilinear-routed wirelength of the layout.

    Each *mid* net is grown as a Steiner tree: pick one terminal, then
    BFS from the current tree to the nearest remaining terminal, attach
    that path, repeat. Component bounding boxes block the grid (pin
    cells stay open). *Rail* nets pay the sum of vertical stub lengths
    from each pin up to the rail trunk.

    When ``clearance=True`` the search becomes Dijkstra with a per-cell
    penalty band (cells adjacent to a blocked bbox cost +2; two-away
    cells cost +1). This mirrors :class:`_RouteGrid` and surfaces the
    U-shape detour that occurs when two coplanar pins both sit on a
    bbox edge — a flipped or mirrored component often hides such a
    loop, and the plain BFS variant is loop-blind.

    ``cross_coupled_xnets`` lists ``(net_w1, net_w2, a_id, b_id)`` for
    every cross-coupled FET pair the X-router will rewrite into a
    single diagonal (see :mod:`autodraw_hacks`). Those two coupling
    nets are *excluded* from the Steiner-tree loop and replaced with a
    fixed cost ``HPWL(arm_1) + HPWL(arm_2) + crossing_weight·grid_px``.
    The Steiner-grow accounting penalises shared-row coupling-net
    overlap with a ghost crossing per cell, so without this hook the
    SA's "real" cost actively biases away from the row-aligned
    placement the X-router needs.
    """
    from collections import deque

    grid_w = max(2, int(canvas_w / grid_px) + 2)
    grid_h = max(2, int(canvas_h / grid_px) + 2)

    blocked = bytearray(grid_w * grid_h)

    def cell_idx(x: int, y: int) -> int:
        return x * grid_h + y

    for x0, y0, x1, y1 in boxes:
        cx0 = max(0, int((x0 + 1) / grid_px))
        cx1 = min(grid_w, int((x1 - 1) / grid_px) + 1)
        cy0 = max(0, int((y0 + 1) / grid_px))
        cy1 = min(grid_h, int((y1 - 1) / grid_px) + 1)
        for x in range(cx0, cx1):
            base = x * grid_h
            for y in range(cy0, cy1):
                blocked[base + y] = 1

    pin_cells: dict[tuple[int, str], tuple[int, int]] = {}
    for key, (px, py) in pins.items():
        cx = max(0, min(grid_w - 1, int(round(px / grid_px))))
        cy = max(0, min(grid_h - 1, int(round(py / grid_px))))
        blocked[cell_idx(cx, cy)] = 0
        pin_cells[key] = (cx, cy)

    # Per-cell clearance penalty (only used when clearance=True).
    # Mirrors _RouteGrid.finalize: distance-1 → 2, distance-2 → 1.
    clr = bytearray(grid_w * grid_h)
    if clearance:
        from collections import deque as _deque
        dist = bytearray(grid_w * grid_h)
        for i in range(len(dist)):
            dist[i] = 3
        cq: "_deque[tuple[int, int]]" = _deque()
        for x in range(grid_w):
            base = x * grid_h
            for y in range(grid_h):
                if blocked[base + y]:
                    dist[base + y] = 0
                    cq.append((x, y))
        while cq:
            x, y = cq.popleft()
            d = dist[cell_idx(x, y)]
            if d >= 2:
                continue
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                if not (0 <= nx < grid_w and 0 <= ny < grid_h):
                    continue
                if dist[cell_idx(nx, ny)] > d + 1:
                    dist[cell_idx(nx, ny)] = d + 1
                    cq.append((nx, ny))
        for i in range(len(dist)):
            if blocked[i]:
                clr[i] = 0
            elif dist[i] == 1:
                clr[i] = 2
            elif dist[i] == 2:
                clr[i] = 1
            else:
                clr[i] = 0

    total = 0.0
    crossings = 0
    cell_owner = bytearray(grid_w * grid_h)

    # ---- Cross-coupled FET pairs: replace their two coupling nets
    # ----  with one fixed "X" cost and skip them in the Steiner loop.
    x_net_keys: set[str] = set()
    if cross_coupled_xnets:
        for net_w1, net_w2, a_id, b_id in cross_coupled_xnets:
            x_net_keys.add(net_w1)
            x_net_keys.add(net_w2)
            try:
                a_g = pins[(a_id, "gate")]
                a_d = pins[(a_id, "drain")]
                b_g = pins[(b_id, "gate")]
                b_d = pins[(b_id, "drain")]
            except KeyError:
                continue
            # Two arms, Manhattan length each (HPWL of the two endpoints).
            # The router will redraw them as diagonals of similar length;
            # using HPWL keeps the cost rational/scale-comparable to the
            # other Steiner contributions in this function.
            arm1 = abs(a_g[0] - b_d[0]) + abs(a_g[1] - b_d[1])
            arm2 = abs(b_g[0] - a_d[0]) + abs(b_g[1] - a_d[1])
            total += (arm1 + arm2) / grid_px       # divided out below
            crossings += 1                          # the single X crossing

    # ---- Mid nets via Steiner-tree search (BFS or Dijkstra) ----
    for net_id, (_net_key, terms) in enumerate(mid_nets.items(), start=1):
        if _net_key in x_net_keys:
            continue
        owner_id = (net_id % 254) + 1
        first_cell = pin_cells[terms[0]]
        tree_cells: set[tuple[int, int]] = {first_cell}
        cell_owner[cell_idx(*first_cell)] = owner_id

        remaining_targets = {pin_cells[t] for t in terms[1:]}
        remaining_targets -= tree_cells

        while remaining_targets:
            parent: dict[tuple[int, int], Optional[tuple[int, int]]] = {}
            hit: Optional[tuple[int, int]] = None

            if not clearance:
                q: deque[tuple[int, int]] = deque()
                for c in tree_cells:
                    parent[c] = None
                    q.append(c)
                while q and hit is None:
                    x, y = q.popleft()
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        nx, ny = x + dx, y + dy
                        if not (0 <= nx < grid_w and 0 <= ny < grid_h):
                            continue
                        if (nx, ny) in parent:
                            continue
                        if blocked[cell_idx(nx, ny)] and (nx, ny) not in remaining_targets:
                            continue
                        parent[(nx, ny)] = (x, y)
                        if (nx, ny) in remaining_targets:
                            hit = (nx, ny)
                            break
                        q.append((nx, ny))
            else:
                from heapq import heappush, heappop
                cell_dist: dict[tuple[int, int], int] = {}
                heap: list[tuple[int, int, int]] = []
                for c in tree_cells:
                    parent[c] = None
                    cell_dist[c] = 0
                    heappush(heap, (0, c[0], c[1]))
                while heap and hit is None:
                    cost, x, y = heappop(heap)
                    if cost > cell_dist.get((x, y), cost):
                        continue
                    if (x, y) in remaining_targets and (x, y) not in tree_cells:
                        hit = (x, y)
                        break
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        nx, ny = x + dx, y + dy
                        if not (0 <= nx < grid_w and 0 <= ny < grid_h):
                            continue
                        ci = cell_idx(nx, ny)
                        if blocked[ci] and (nx, ny) not in remaining_targets:
                            continue
                        step = 1 + clr[ci]
                        ncost = cost + step
                        if ncost < cell_dist.get((nx, ny), 10 ** 9):
                            cell_dist[(nx, ny)] = ncost
                            parent[(nx, ny)] = (x, y)
                            heappush(heap, (ncost, nx, ny))

            if hit is None:
                total += unroutable_penalty
                remaining_targets.pop()
                continue

            path_len = 0
            cur: Optional[tuple[int, int]] = hit
            while cur is not None:
                ci = cell_idx(*cur)
                if cur not in tree_cells:
                    if cell_owner[ci] != 0 and cell_owner[ci] != owner_id:
                        crossings += 1
                    cell_owner[ci] = owner_id
                    tree_cells.add(cur)
                cur = parent[cur]
                path_len += 1
            total += path_len - 1
            remaining_targets.discard(hit)

    # ---- Rail nets: vertical stubs to the trunk ----
    for net_key, terms in rail_nets.items():
        on_top = (net_key in canonical_top)
        rail_y_cell = max(0, min(grid_h - 1, int(round(
            (rail_top_y if on_top else rail_bot_y) / grid_px))))
        for t in terms:
            cx, cy = pin_cells[t]
            stub = abs(cy - rail_y_cell)
            total += stub
            for yy in range(min(cy, rail_y_cell), max(cy, rail_y_cell) + 1):
                ci = cell_idx(cx, yy)
                if cell_owner[ci] == 0:
                    cell_owner[ci] = 255

    return total * grid_px + crossing_weight * crossings * grid_px


def _route_total_hpwl(
    pins: dict[tuple[int, str], tuple[float, float]],
    boxes: Sequence[tuple[float, float, float, float]],
    mid_nets: dict[str, list[tuple[int, str]]],
    rail_nets: dict[str, list[tuple[int, str]]],
    canvas_w: float,
    canvas_h: float,
    rail_top_y: float,
    rail_bot_y: float,
    canonical_top: set[str],
    grid_px: int = SA_GRID_PX,                 # accepted for API parity
    crossing_weight: float = 70.0,
    unroutable_penalty: float = 0.0,
) -> float:
    """Half-perimeter wirelength + bbox-interleave crossings + rail stubs.

    Same call signature as :func:`_route_total_wirelength` so they're
    swap-in. HPWL is a tight lower bound on rectilinear Steiner tree
    length and ignores blocked component bodies, so it is much cheaper
    to evaluate at the cost of being optimistic — which is fine for SA
    exploration. The bbox-interleave term keeps SA from collapsing
    everything onto the same row.
    """
    bboxes: list[tuple[float, float, float, float]] = []
    total = 0.0
    for terms in mid_nets.values():
        if len(terms) < 2:
            continue
        xs = [pins[t][0] for t in terms]
        ys = [pins[t][1] for t in terms]
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)
        total += (x1 - x0) + (y1 - y0)
        bboxes.append((x0, x1, y0, y1))

    crossings = 0.0
    for i in range(len(bboxes)):
        x0a, x1a, y0a, y1a = bboxes[i]
        for j in range(i + 1, len(bboxes)):
            x0b, x1b, y0b, y1b = bboxes[j]
            if x1a < x0b or x1b < x0a or y1a < y0b or y1b < y0a:
                continue
            x_cont = (x0a <= x0b and x1a >= x1b) or (x0b <= x0a and x1b >= x1a)
            y_cont = (y0a <= y0b and y1a >= y1b) or (y0b <= y0a and y1b >= y1a)
            if x_cont and y_cont:
                crossings += 0.25
            elif x_cont or y_cont:
                crossings += 0.5
            else:
                crossings += 1.0

    # Rail stubs — vertical drops to the trunk on the appropriate rail.
    for net_key, terms in rail_nets.items():
        on_top = (net_key in canonical_top)
        rail_y = rail_top_y if on_top else rail_bot_y
        for t in terms:
            _px, py = pins[t]
            total += abs(py - rail_y)

    return total + crossing_weight * crossings


def _enforce_min_pitch(
    branches: Sequence[_Branch],
    y_pos: dict[int, float],
    rail_top_y: float,
    rail_bot_y: float,
    min_gap: float = MIN_GAP,
    junctions: Optional[Sequence[dict]] = None,
) -> None:
    """Push y values into a feasible region.

    Row order is preserved; consecutive boxes in a column must keep at
    least ``min_gap`` of edge-to-edge clearance, and every box must
    fit between the two rail bounds. Output y values are snapped to
    ``GRID_PX`` (rounded *up* to the next multiple) so the subsequent
    snap in ``_layout`` is a no-op — otherwise the snap could shrink
    the post-enforcement gap below ``min_gap`` by up to GRID_PX. In
    place, idempotent.

    If ``junctions`` is given (from
    ``autodraw_hacks.detect_spine_junctions``), spine pins meeting at
    each junction are kept apart by at least the
    cross-column ``cross_gap = max(min_gap, 2 * PORT_LEN)``: every
    "below" branch's top pin must sit that far under the lowest
    "above" branch's bot pin. The trunk wire then has clear space to
    run horizontally between the two box rows instead of along their
    coinciding bbox edges. The deficit (if any) is split symmetrically
    — above branches lift up, below branches drop down — so the
    constraint is satisfied without unfairly biasing either side.
    """
    upper = rail_top_y + RAIL_GAP
    lower = rail_bot_y - RAIL_GAP
    cross_gap = max(min_gap, 2 * PORT_LEN)

    def snap_up(v: float) -> float:
        # Round up to a multiple of GRID_PX so the gap to the previous
        # row never shrinks under snap-to-grid.
        import math
        return math.ceil(v / GRID_PX) * GRID_PX

    def snap_down(v: float) -> float:
        import math
        return math.floor(v / GRID_PX) * GRID_PX

    def per_branch_sweep(branch_iter: Iterable[_Branch]) -> None:
        for branch in branch_iter:
            descs = branch.descs
            if not descs:
                continue
            # Forward sweep: ensure each component sits below its predecessor.
            prev_bot = upper
            for d in descs:
                min_top = prev_bot if prev_bot == upper else prev_bot + min_gap
                min_center = min_top + d.bbox_h / 2.0
                cur = max(y_pos[id(d)], min_center)
                cur = snap_up(cur - d.bbox_h / 2.0) + d.bbox_h / 2.0
                y_pos[id(d)] = cur
                prev_bot = cur + d.bbox_h / 2.0
            # Back sweep: clamp the bottom of the column to the bot rail.
            next_top = lower
            for d in reversed(descs):
                max_center = next_top - d.bbox_h / 2.0
                cur = min(y_pos[id(d)], max_center)
                cur = snap_down(cur - d.bbox_h / 2.0) + d.bbox_h / 2.0
                y_pos[id(d)] = cur
                next_top = cur - d.bbox_h / 2.0 - min_gap

    per_branch_sweep(branches)

    if junctions:
        # Diff-pair-tail style junction clearance lives in autodraw_hacks
        # (lazy import to avoid a circular import at module load).
        from sycan.autodraw_hacks import apply_junction_clearance
        apply_junction_clearance(
            branches, y_pos, junctions, min_gap, per_branch_sweep,
        )


def _sa_optimize(
    branches: list[_Branch],
    descs: Sequence[_CompDesc],
    uf: _UF,
    canonical_top: set[str],
    canonical_bot: set[str],
    rail_top_y: float,
    rail_bot_y: float,
    canvas_w: float,
    canvas_h: float,
    *,
    iterations: Optional[int] = None,
    seed: int = 0,
    cost_model: str = "hpwl",
    junctions: Optional[Sequence[dict]] = None,
    reverse_isolated_branches: bool = False,
) -> tuple[
    list[int],
    dict[int, float],
    dict[int, bool],
    dict[int, bool],
    float,
    float,
]:
    """Anneal column order + per-component y + mirror + (free) spine
    flip.

    ``cost_model`` selects the SA's inner cost:

    * ``"hpwl"`` (default) — half-perimeter wirelength plus a bbox-
      interleave crossing penalty plus rail-stub lengths. Fast.
    * ``"real"`` — actual rectilinear-routed wirelength via a Steiner-
      tree BFS over a coarse routing grid that knows about component
      bounding boxes. Slower but accounts for routing detours, so the
      final layouts can be tighter on circuits where wires would
      otherwise be forced around blocks.

    Returns ``(col_order, y_pos, mirrors, flips, initial_cost, best_cost)``.
    """
    import math
    import random

    n = len(branches)
    if n == 0:
        return ([], {}, {}, {}, 0.0, 0.0)

    branch_of: dict[int, int] = {}
    for b_idx, branch in enumerate(branches):
        for d in branch.descs:
            branch_of[id(d)] = b_idx

    mid_nets, rail_nets = _build_routable_nets(
        descs, uf, canonical_top, canonical_bot,
    )

    # Per-branch widths derived from the actual component bbox sizes.
    col_widths = _column_widths(branches)

    # SA is interesting only when there is something to permute or align.
    if n <= 1 and not mid_nets:
        return (
            list(range(n)),
            _default_y_positions(
                branches, rail_top_y, rail_bot_y,
                canonical_top, canonical_bot, uf,
            ),
            {}, {}, 0.0, 0.0,
        )

    # Initial state.
    col_order = list(range(n))
    y_pos = _default_y_positions(
        branches, rail_top_y, rail_bot_y,
        canonical_top, canonical_bot, uf,
    )
    mirrors: dict[int, bool] = {id(d): d.mirror for d in descs}
    flips: dict[int, bool] = {id(d): d.flip for d in descs}

    # Components SA may freely flip vertically: only those whose spine
    # endpoints are *not* connected to either rail.
    flippable = [
        d for d in descs
        if not d.rail_anchored
        and uf.find(d.port_net(d.spine_top)) not in canonical_top
        and uf.find(d.port_net(d.spine_top)) not in canonical_bot
        and uf.find(d.port_net(d.spine_bot)) not in canonical_top
        and uf.find(d.port_net(d.spine_bot)) not in canonical_bot
        and d.spine_top != d.spine_bot
    ]
    mirrorable = [d for d in descs if d.side_ports]

    # Pre-detect cross-coupled FET pairs once. The "real" cost function
    # uses this to substitute a flat X-cost for the two coupling nets
    # so the SA stops penalising the row-aligned placement the X-router
    # actually wants. HPWL ignores it (no pathology to fix there).
    from sycan.autodraw_hacks import detect_cross_coupled_descs
    x_pairs = detect_cross_coupled_descs(descs, uf)
    x_pairs_for_cost = [
        (net_w1, net_w2, id(a), id(b))
        for a, b, net_w1, net_w2 in x_pairs
    ]

    cost_fn = _route_total_hpwl if cost_model == "hpwl" else _route_total_wirelength

    def evaluate(co, yp, mi, fl):
        centers, total_w = _column_centers(branches, co, col_widths, PAD)
        pins, boxes = _pin_positions_for_state(
            descs, branch_of, co, centers, yp, mi, fl,
        )
        kw: dict = {}
        if cost_model != "hpwl":
            kw["cross_coupled_xnets"] = x_pairs_for_cost
        return cost_fn(
            pins, boxes, mid_nets, rail_nets,
            total_w, canvas_h, rail_top_y, rail_bot_y, canonical_top,
            **kw,
        )

    cost = evaluate(col_order, y_pos, mirrors, flips)
    initial_cost = cost
    best_co = col_order[:]
    best_yp = dict(y_pos)
    best_mi = dict(mirrors)
    best_fl = dict(flips)
    best_cost = cost

    # ---- Iteration count: scale with the search space, but cap. ----
    n_descs = len(descs)
    if iterations is None:
        iterations = 600 + 80 * n + 60 * n_descs
    iterations = min(iterations, 8000)
    if iterations <= 0 or cost == 0:
        return (best_co, best_yp, best_mi, best_fl, initial_cost, best_cost)

    rng = random.Random(seed)
    T = max(50.0, cost * 0.25)
    Tmin = max(0.5, T / 600.0)
    decay = (Tmin / T) ** (1.0 / iterations)

    # Move probabilities. y-perturbation gets the biggest share since
    # vertical alignment is the strongest lever in this cost model.
    moves: list[tuple[str, float]] = []
    moves.append(("swap_columns", 0.15 if n >= 2 else 0.0))
    moves.append(("mirror", 0.13 if mirrorable else 0.0))
    moves.append(("flip", 0.05 if flippable else 0.0))
    moves.append(("perturb_y", 0.27))
    moves.append(("shift_column", 0.18))
    moves.append(("align_y", 0.17))
    moves.append(("snap_y_to_rail_partner", 0.18))
    total_w = sum(w for _, w in moves)
    moves = [(name, w / total_w) for name, w in moves if w > 0]
    move_names = [m[0] for m in moves]
    move_cum = []
    s = 0.0
    for _, w in moves:
        s += w
        move_cum.append(s)

    def pick_move() -> str:
        r = rng.random()
        for name, c in zip(move_names, move_cum):
            if r <= c:
                return name
        return move_names[-1]

    improved = 0
    for it in range(iterations):
        kind = pick_move()
        new_co = col_order
        new_yp = y_pos
        new_mi = mirrors
        new_fl = flips

        if kind == "swap_columns":
            i, j = rng.sample(range(n), 2)
            new_co = col_order[:]
            new_co[i], new_co[j] = new_co[j], new_co[i]

        elif kind == "mirror":
            d = rng.choice(mirrorable)
            new_mi = dict(mirrors)
            new_mi[id(d)] = not new_mi[id(d)]

        elif kind == "flip":
            d = rng.choice(flippable)
            new_fl = dict(flips)
            new_fl[id(d)] = not new_fl[id(d)]

        elif kind == "perturb_y":
            d = rng.choice(descs)
            sigma = max(8.0, MIN_PITCH * 0.35)
            delta = rng.gauss(0.0, sigma)
            new_yp = dict(y_pos)
            new_yp[id(d)] += delta
            _enforce_min_pitch(
                branches, new_yp, rail_top_y, rail_bot_y,
                junctions=junctions,
            )

        elif kind == "shift_column":
            b_idx = rng.randrange(n)
            sigma = max(12.0, MIN_PITCH * 0.5)
            delta = rng.gauss(0.0, sigma)
            new_yp = dict(y_pos)
            for d in branches[b_idx].descs:
                new_yp[id(d)] += delta
            _enforce_min_pitch(
                branches, new_yp, rail_top_y, rail_bot_y,
                junctions=junctions,
            )

        elif kind == "align_y":
            d = rng.choice(descs)
            other_branch = rng.randrange(n)
            if other_branch == branch_of[id(d)] or not branches[other_branch].descs:
                continue
            target = rng.choice(branches[other_branch].descs)
            new_yp = dict(y_pos)
            new_yp[id(d)] = y_pos[id(target)]
            _enforce_min_pitch(
                branches, new_yp, rail_top_y, rail_bot_y,
                junctions=junctions,
            )

        else:  # snap_y_to_rail_partner
            # Pin-level snap: pick a port on ``d`` and a partner port on
            # another component sharing the *same* net, then move ``d``
            # so its pin sits at the partner's pin y. This is what makes
            # e.g. ITAIL's ``+`` pin land at M1.S's y on the diff-pair
            # tail trunk — center-snap alone (the previous version)
            # could never align them when the components had different
            # heights or different pin offsets.
            d = rng.choice(descs)
            ports = list(d.side_ports) + [d.spine_top, d.spine_bot]
            port = rng.choice(ports)
            net = uf.find(d.port_net(port))
            partners: list[tuple[_CompDesc, str]] = []
            for e in descs:
                if id(e) == id(d):
                    continue
                for e_port in (e.spine_top, e.spine_bot, *e.side_ports):
                    if uf.find(e.port_net(e_port)) == net:
                        partners.append((e, e_port))
            if not partners:
                continue
            partner_d, partner_port = rng.choice(partners)

            def pin_y_offset(comp: _CompDesc, p: str, fl: bool) -> float:
                """Pin's y minus comp.cy, given the spine-flip state."""
                _ox, oy = comp.port_offsets.get(p, (0.0, comp.bbox_h / 2.0))
                if fl:
                    oy = comp.bbox_h - oy
                return oy - comp.bbox_h / 2.0

            d_off = pin_y_offset(d, port, flips.get(id(d), d.flip))
            partner_off = pin_y_offset(
                partner_d, partner_port,
                flips.get(id(partner_d), partner_d.flip),
            )
            target_pin_y = y_pos[id(partner_d)] + partner_off
            target_d_cy = target_pin_y - d_off
            new_yp = dict(y_pos)
            new_yp[id(d)] = target_d_cy
            _enforce_min_pitch(
                branches, new_yp, rail_top_y, rail_bot_y,
                junctions=junctions,
            )

        new_cost = evaluate(new_co, new_yp, new_mi, new_fl)
        delta = new_cost - cost
        if delta <= 0.0 or rng.random() < math.exp(-delta / T):
            col_order = new_co
            y_pos = new_yp
            mirrors = new_mi
            flips = new_fl
            cost = new_cost
            if cost < best_cost - 1e-6:
                best_cost = cost
                best_co = col_order[:]
                best_yp = dict(y_pos)
                best_mi = dict(mirrors)
                best_fl = dict(flips)
                improved += 1
        T *= decay

    # ---- Greedy flip / branch-reversal pass --------------------------
    # SA's stochastic flip moves can miss a deterministic improvement —
    # the per-iteration probability is low (0.05) and the HPWL cost is
    # loop-blind (it doesn't see U-shape detours that arise when an
    # upside-down component leaves both wired pins on the same bbox
    # edge). After the main loop, sweep two deterministic moves over
    # the best state until neither helps:
    #
    #   1. Toggle a single component's spine flip.
    #   2. Reverse the entire direction of an "isolated" branch (one
    #      with no rail-anchored desc at either end — _build_branches
    #      picked a direction for it greedily, but topologically both
    #      are equally valid). Reversal toggles every desc's flip and
    #      reverses the relative y_pos within the branch's span; the
    #      single-flip move can't find this because toggling one desc
    #      in a 2+ chain breaks spine abutment and balloons the cost.
    #
    # Each move is evaluated with the clearance-aware routed cost, a
    # closer proxy to final routing than HPWL or plain Steiner BFS, and
    # accepted only when it strictly lowers the cost. Mirror is
    # intentionally excluded from both moves: it's load-bearing for
    # the cross-coupled-X detection in autodraw_hacks (gates have to
    # face inward) and the cost function can't see that constraint.
    isolated_branches = [
        b for b in branches
        if len(b.descs) >= 2 and not any(d.rail_anchored for d in b.descs)
    ] if reverse_isolated_branches else []

    if flippable or isolated_branches:
        def real_eval_clr(co, yp, mi, fl):
            centers, total_w = _column_centers(branches, co, col_widths, PAD)
            pins, boxes = _pin_positions_for_state(
                descs, branch_of, co, centers, yp, mi, fl,
            )
            return _route_total_wirelength(
                pins, boxes, mid_nets, rail_nets,
                total_w, canvas_h, rail_top_y, rail_bot_y,
                canonical_top, clearance=True,
                cross_coupled_xnets=x_pairs_for_cost,
            )

        cur_clr = real_eval_clr(best_co, best_yp, best_mi, best_fl)
        changed = True
        while changed:
            changed = False
            for d in flippable:
                trial = dict(best_fl)
                trial[id(d)] = not trial[id(d)]
                new_clr = real_eval_clr(best_co, best_yp, best_mi, trial)
                if new_clr < cur_clr - 1e-6:
                    best_fl = trial
                    cur_clr = new_clr
                    changed = True
            # Whole-branch reversal sweep for isolated branches.
            for branch in isolated_branches:
                trial_fl = dict(best_fl)
                trial_yp = dict(best_yp)
                for d in branch.descs:
                    trial_fl[id(d)] = not trial_fl[id(d)]
                ys = [best_yp[id(d)] for d in branch.descs]
                for d, y in zip(branch.descs, reversed(ys)):
                    trial_yp[id(d)] = y
                new_clr = real_eval_clr(best_co, trial_yp, best_mi, trial_fl)
                if new_clr < cur_clr - 1e-6:
                    best_fl = trial_fl
                    best_yp = trial_yp
                    cur_clr = new_clr
                    # Reverse branch.descs so _enforce_min_pitch (run
                    # in autodraw() after this returns) reads the descs
                    # in their new top-to-bottom order — otherwise its
                    # forward sweep would shuffle the y values back
                    # toward the un-reversed direction.
                    branch.descs.reverse()
                    changed = True

    return best_co, best_yp, best_mi, best_fl, initial_cost, best_cost


# ---------------------------------------------------------------------------
# Top-level entry.
# ---------------------------------------------------------------------------
# Sentinel used to distinguish "user explicitly disabled glyphs"
# (``res_dir=None``) from "user didn't specify; use the default
# location" (``res_dir=_DEFAULT_RES``).
_DEFAULT_RES = object()

# Default location for glyph SVGs: ``<repo>/res/``. ``Path(__file__)`` is
# ``<repo>/src/sycan/autodraw.py``; two ``.parent`` hops back up to the
# package root and another to the source-tree root. When sycan is
# installed without a sibling ``res/`` directory the loader silently
# falls back to no-glyph rendering, so this default is harmless even
# when the layout is wrong.
_DEFAULT_RES_DIR = Path(__file__).resolve().parent.parent.parent / "res"


def autodraw(
    circuit: Union[Circuit, str],
    power_nets: Sequence[str] = ("VDD", "VSS", "VEE"),
    *,
    filename: Optional[Union[str, Path]] = None,
    optimize: bool = True,
    iterations: Optional[int] = None,
    seed: int = 0,
    cost_model: str = "hpwl",
    res_dir: Union[str, Path, None, object] = _DEFAULT_RES_DIR,
    reverse_isolated_branches: bool = False,
    back_annotation: Optional[dict[str, Sequence[str]]] = None,
    max_retries: int = 5,
) -> str:
    """Render ``circuit`` to an SVG schematic and return the SVG string.

    Parameters
    ----------
    circuit:
        Either a :class:`~sycan.Circuit` or a SPICE netlist string.
    power_nets:
        Power-rail nets. Names matching ``VDD`` / ``VCC`` / ``VPP``
        anchor the top rail; ``VSS`` / ``VEE`` / ``GND`` and the SPICE
        ground node ``"0"`` anchor the bottom rail. Anything else
        passed in is treated as a top-rail node by default.
    filename:
        Optional path to write the SVG to.
    optimize:
        If ``True`` (default), run a simulated-annealing pass over the
        column order, per-component y position, side-port mirror, and
        (where topologically free) spine flip. Disable for a
        deterministic baseline when debugging the placer.
    iterations:
        Override the SA iteration count. ``None`` lets the optimizer
        scale with circuit size.
    seed:
        RNG seed for SA reproducibility.
    cost_model:
        ``"hpwl"`` (default, fast) — HPWL + bbox-interleave crossings
        + rail stubs. ``"real"`` — actual rectilinear-routed wirelength
        via grid BFS that knows about component bodies; slower but
        accounts for routing detours.
    res_dir:
        Folder with per-kind SVG glyphs (``res/nmos.svg``,
        ``res/res.svg``, ...). Defaults to ``<repo>/res/`` so the
        bundled symbols render automatically; pass an explicit path
        to use your own glyph library, or ``None`` to disable glyphs
        and draw the labelled-rect placeholders instead. Components
        whose kinds are missing from the chosen directory fall back
        to the rect.
    reverse_isolated_branches:
        If ``True``, the post-SA greedy pass also considers reversing
        the column direction of each "isolated" branch (one with no
        rail-anchored desc at either end). The greedy walker in
        ``_build_branches`` picks a direction (top→bot or bot→top)
        for such branches arbitrarily, but topologically both are
        equally valid; this move tries the opposite and keeps it when
        routed wirelength drops. Off by default — current circuits
        produce no fully-isolated multi-component branches, so this
        is dormant capability for circuits / branch-builder changes
        that introduce them.
    back_annotation:
        Optional mapping of component name → sequence of annotation
        strings. Each string is rendered as a thin orange line of text
        on the right-hand side of the matching component, stacked top-
        to-bottom. Useful for surfacing derived results (operating
        points, gain numbers, noise figures, …) next to the parts they
        belong to. Annotations are drawn as the very last layer so
        they sit on top of every other primitive; collisions with
        wires or labels are intentionally ignored — the caller chooses
        what to display.
    max_retries:
        Maximum number of fallback render passes triggered when the
        first pass produces wires that lay collinear on top of one
        another. Each retry re-runs the SA + routing with the next
        seed from a fixed sequence (:data:`_RETRY_SEEDS`); the first
        pass whose total overlap length is at or below
        :data:`_OVERLAP_TOL` is accepted. If every attempt has overlap
        the lowest-overlap render is returned. Default is 5; pass 0 to
        disable the loop.
    """
    if isinstance(circuit, str):
        from sycan.spice import parse
        circuit = parse(circuit)

    # Classify rails. User-supplied names override defaults but do NOT
    # remove the conventional ones — circuits routinely mix VDD with
    # GND/0, so we always keep "0" on the bottom.
    top_set: set[str] = {n for n in _TOP_RAIL_DEFAULT}
    bot_set: set[str] = {n for n in _BOT_RAIL_DEFAULT}
    for n in power_nets:
        u = n.upper()
        if u in {"VDD", "VCC", "VPP"} or u.startswith("VDD") or u.startswith("VCC"):
            top_set.add(n)
        elif u in {"VSS", "VEE", "GND"} or u.startswith("VSS") or u.startswith("VEE"):
            bot_set.add(n)
        else:
            # Unknown rails default to the top.
            top_set.add(n)

    # Wire-shorts: SPICE ``W`` parses as a 0-V VoltageSource. Fold those
    # plus explicit GND ties into a union-find on net names.
    uf = _UF()
    for c in circuit.components:
        if isinstance(c, VoltageSource) and c.name.upper().startswith("W"):
            uf.union(c.n_plus, c.n_minus)
        elif isinstance(c, GND):
            uf.union(c.node, "0")

    # Apply rail aliases (e.g., a user-defined "VDD_low" treated as VDD).
    canonical_top: set[str] = {uf.find(n) for n in top_set}
    canonical_bot: set[str] = {uf.find(n) for n in bot_set}

    # Visible components: skip wires and GND markers (they exist only
    # to merge nets).
    descs: list[_CompDesc] = []
    for c in circuit.components:
        if isinstance(c, VoltageSource) and c.name.upper().startswith("W"):
            continue
        if isinstance(c, GND):
            continue
        descs.append(_describe(c))

    # ---- Branch finding ----
    branches, spine_index = _build_branches(
        descs, uf, canonical_top, canonical_bot,
    )

    # ---- Glyph load + per-component bbox / port_offsets ----
    # Done *before* layout so that placement, the SA cost, and the
    # routing all use each component's actual bounding-box dimensions.
    if res_dir is _DEFAULT_RES:
        res_dir = _DEFAULT_RES_DIR if _DEFAULT_RES_DIR.exists() else None
    glyphs = _load_glyphs(res_dir)
    _apply_glyphs(descs, glyphs)

    # ---- Placement ----
    n_cols = max(1, len(branches))
    rail_top_y = PAD

    # Canvas height: enough for the tallest branch *and* for any
    # logical chain that fans across columns through a spine junction.
    # The diff-pair tail is the canonical case — [R, M] ends at the
    # junction and ITAIL starts there, so the canvas must fit 3 rows
    # so ITAIL can drop down far enough to align its + pin with M's S.
    phys_max = max(
        (sum(d.bbox_h for d in b.descs)
         + max(0, len(b.descs) - 1) * MIN_GAP
         for b in branches if b.descs),
        default=float(BOX_H),
    )
    max_logical = _logical_chain_length(branches, uf, spine_index)
    tallest_box = max(
        (d.bbox_h for b in branches for d in b.descs),
        default=float(BOX_H),
    )
    # Cross-column gap at a junction has to fit two pin stubs (one
    # from each side of the junction trunk), not just one MIN_GAP.
    cross_gap = max(MIN_GAP, 2 * PORT_LEN)
    logical_h = (
        max_logical * tallest_box
        + max(0, max_logical - 1) * cross_gap
    )
    max_stack_h = max(phys_max, logical_h)
    rail_bot_y = rail_top_y + 2 * RAIL_GAP + max(float(BOX_H), max_stack_h) + 2 * PORT_LEN
    x0 = PAD
    col_widths = _column_widths(branches)
    initial_col_order = list(range(len(branches)))
    _, canvas_w = _column_centers(branches, initial_col_order, col_widths, x0)
    canvas_h = rail_bot_y + PAD

    # ---- Spine junctions (autodraw_hacks override) ----
    # Pre-compute junction info so ``_enforce_min_pitch`` (called both
    # post-SA and inside SA's inner loop) can drive apart pins meeting
    # at a junction net, preventing the trunk from collapsing onto a
    # bbox edge of either side. Branch-object refs survive SA's column
    # reorder, so this list stays valid both before and after.
    from sycan.autodraw_hacks import detect_spine_junctions
    junctions = detect_spine_junctions(branches, uf, spine_index)

    # ---- Retry loop ----
    # Run the seed-dependent placement + routing + emission, then check
    # whether the rendered polylines have collinear segments lying on
    # top of one another (visually, two distinct nets fused into a
    # single line — see :func:`_segment_overlap_length`). If they do,
    # restart the pass with the next seed from ``_RETRY_SEEDS`` and
    # keep the lowest-overlap render seen so far. The loop exits as
    # soon as a pass produces overlap at or below ``_OVERLAP_TOL``.
    _retry_initial_branches = list(branches)
    _retry_initial_mirrors = {id(d): d.mirror for d in descs}
    _retry_initial_flips = {id(d): d.flip for d in descs}

    _retry_seed_queue: list[int] = [seed]
    for _retry_s in _RETRY_SEEDS:
        if _retry_s not in _retry_seed_queue:
            _retry_seed_queue.append(_retry_s)

    _retry_attempts_left = max(1, max_retries + 1)
    _retry_best_svg: Optional[str] = None
    _retry_best_overlap: Optional[float] = None

    while _retry_seed_queue and _retry_attempts_left > 0:
        seed = _retry_seed_queue.pop(0)
        _retry_attempts_left -= 1

        # Reset state mutated by a previous pass before re-running SA.
        branches = list(_retry_initial_branches)
        for d in descs:
            d.mirror = _retry_initial_mirrors[id(d)]
            d.flip = _retry_initial_flips[id(d)]

        # ---- SA: column order + per-component y + mirror + spine flip ----
        if optimize and (len(branches) >= 2 or any(
            len(b.descs) >= 1 and b.descs[0].side_ports for b in branches
        )):
            col_order, y_pos, mirrors, flips, _init, _final = _sa_optimize(
                branches, descs, uf, canonical_top, canonical_bot,
                rail_top_y, rail_bot_y, canvas_w, canvas_h,
                iterations=iterations, seed=seed, cost_model=cost_model,
                junctions=junctions,
                reverse_isolated_branches=reverse_isolated_branches,
            )
            branches = [branches[i] for i in col_order]
            for d in descs:
                d.mirror = mirrors.get(id(d), d.mirror)
                d.flip = flips.get(id(d), d.flip)
        else:
            y_pos = _default_y_positions(
                branches, rail_top_y, rail_bot_y,
                canonical_top, canonical_bot, uf,
            )

        # Snap y_pos values to the routing grid so component box edges
        # land on grid (the snap inside _layout would otherwise shrink the
        # min-pitch gap by up to GRID_PX).
        _enforce_min_pitch(
            branches, y_pos, rail_top_y, rail_bot_y, junctions=junctions,
        )

        # Recompute widths in the (possibly reordered) branch order, then
        # place columns left to right using those widths.
        col_widths = _column_widths(branches)
        final_col_order = list(range(len(branches)))
        col_centers, canvas_w = _column_centers(
            branches, final_col_order, col_widths, x0,
        )
        placed = _layout(branches, final_col_order, col_widths, col_centers, y_pos)

        # ---- Net inventory ----
        nets = _collect_nets(placed, uf)

        # ---- Routing ----
        grid_w = int(canvas_w / GRID_PX) + 2
        grid_h = int(canvas_h / GRID_PX) + 2
        rg = _RouteGrid(grid_w, grid_h)

        def to_cell(x: float, y: float) -> tuple[int, int]:
            return int(round(x / GRID_PX)), int(round(y / GRID_PX))

        # Block component bodies. Each component carries its own bbox_w /
        # bbox_h (set by _apply_glyphs from the loaded glyph's viewBox),
        # so glyphs of arbitrary sizes are blocked correctly — using the
        # global default BOX_W / BOX_H here would let wires pass through
        # any wide-glyph that's bigger than the default rect.
        for p in placed:
            bw, bh = p.desc.bbox_w, p.desc.bbox_h
            x0c, y0c = to_cell(p.cx - bw / 2, p.cy - bh / 2)
            x1c, y1c = to_cell(p.cx + bw / 2, p.cy + bh / 2)
            rg.block_rect(x0c, y0c, x1c + 1, y1c + 1)

        # Allow each pin tip cell to be "passable" even if the box body is
        # blocked next to it. For *lateral* pins (left/right side), also
        # carve a one-row clearance channel from the pin out through the
        # bbox edge: when the pin sits a few cells inside the bbox right
        # edge (e.g. an NPN base whose port circle is offset inward from
        # the body), the row between pin and edge would otherwise be
        # blocked, forcing the BFS to fail and the fallback to elbow back
        # into the body. Allowing those cells lets the wire exit in the
        # natural lateral direction the pin already faces.
        for p in placed:
            bw, bh = p.desc.bbox_w, p.desc.bbox_h
            bx0c, by0c = to_cell(p.cx - bw / 2, p.cy - bh / 2)
            bx1c, by1c = to_cell(p.cx + bw / 2, p.cy + bh / 2)
            for port, (px, py) in p.pin_pos.items():
                cell = to_cell(px, py)
                rg.allow_pin.add(cell)
                side = p.pin_side.get(port)
                if side == "right":
                    for xx in range(cell[0] + 1, bx1c + 1):
                        rg.allow_pin.add((xx, cell[1]))
                elif side == "left":
                    for xx in range(bx0c, cell[0]):
                        rg.allow_pin.add((xx, cell[1]))

        # Compute the per-cell clearance-to-nearest-block penalty so the
        # router prefers L/S elbows that sit in open space rather than
        # along a component edge.
        rg.finalize()

        # Rails as horizontal trunks: dedicate a small Y range.
        top_rail_cell = to_cell(0, rail_top_y)[1]
        bot_rail_cell = to_cell(0, rail_bot_y)[1]
        for x in range(grid_w):
            rg.used[x][top_rail_cell] += 0  # nothing yet, just ensures index ok
            rg.used[x][bot_rail_cell] += 0

        # Walk nets. Spine-internal cases first to mark trunks; then everything
        # else gets routed.
        polylines: list[tuple[str, list[tuple[float, float]]]] = []

        # Rails: single horizontal line top and bottom across the full canvas.
        rail_polylines: list[tuple[str, list[tuple[float, float]]]] = []
        if any(uf.find(p.desc.port_net(port)) in canonical_top
               for p in placed for port in p.pin_pos):
            rail_polylines.append(("rail-top",
                                   [(PAD / 2, rail_top_y),
                                    (canvas_w - PAD / 2, rail_top_y)]))
        if any(uf.find(p.desc.port_net(port)) in canonical_bot
               for p in placed for port in p.pin_pos):
            rail_polylines.append(("rail-bot",
                                   [(PAD / 2, rail_bot_y),
                                    (canvas_w - PAD / 2, rail_bot_y)]))

        # For each net: route. Rail-class nets connect each terminal directly
        # to the rail trunk by a vertical stub; mid nets use BFS.
        routed_polylines: list[tuple[str, list[tuple[float, float]]]] = []
        # Order: route rail nets first (mostly trivial vertical stubs), then
        # mid nets in increasing terminal count (simple ones first leave the
        # cleanest space for harder nets).
        net_items = list(nets.items())
        net_items.sort(key=lambda kv: (
            0 if kv[0] in canonical_top or kv[0] in canonical_bot else 1,
            len(kv[1]),
        ))

        # Cross-coupled FET pairs (autodraw_hacks override): the BFS makes
        # visually messy zigzags because the two coupling nets compete for
        # the same column-gap channel. Detect the latch pattern (M_a.gate ↔
        # M_b.drain and M_b.gate ↔ M_a.drain) and pre-build hand-routed
        # symmetric X polylines. Cells along the polylines are *strongly*
        # marked as used so the BFS routes for other nets steer clear and
        # don't visually fuse with the X. The main loop skips these nets.
        from sycan.autodraw_hacks import cross_coupled_pinned_polylines
        cross_polys = cross_coupled_pinned_polylines(placed, nets, uf)

        def _bresenham_cells(c0, c1):
            x0, y0 = c0
            x1, y1 = c1
            dx = abs(x1 - x0)
            dy = abs(y1 - y0)
            sx = 1 if x0 < x1 else -1
            sy = 1 if y0 < y1 else -1
            err = dx - dy
            while True:
                yield x0, y0
                if x0 == x1 and y0 == y1:
                    return
                e2 = 2 * err
                if e2 > -dy:
                    err -= dy
                    x0 += sx
                if e2 < dx:
                    err += dx
                    y0 += sy

        for net_key, polys in cross_polys.items():
            for poly in polys:
                routed_polylines.append((f"net-{net_key}", poly))
                for (sx, sy), (ex, ey) in zip(poly, poly[1:]):
                    c0 = to_cell(sx, sy)
                    c1 = to_cell(ex, ey)
                    for xx, yy in _bresenham_cells(c0, c1):
                        if 0 <= xx < grid_w and 0 <= yy < grid_h:
                            rg.used[xx][yy] += 8

        for net_key, terms in net_items:
            if len(terms) < 1:
                continue
            if net_key in cross_polys:
                # Already emitted as a hand-routed cross.
                continue

            if net_key in canonical_top or net_key in canonical_bot:
                on_top = net_key in canonical_top
                rail_y = rail_top_y if on_top else rail_bot_y
                rail_cell_y = int(round(rail_y / GRID_PX))
                rail_targets = {
                    (xx, rail_cell_y) for xx in range(grid_w)
                    if 0 <= rail_cell_y < grid_h
                    and not rg.blocked[xx][rail_cell_y]
                }
                for p, port in terms:
                    px, py = p.pin_pos[port]
                    side = p.pin_side.get(port)
                    # Top/bot side pins clear their own bbox by exiting along
                    # the spine, so a single straight stub is fine. A pin on
                    # a *lateral* side (e.g. an NMOS gate at the box's left
                    # edge) sits *on* its own bbox column — a straight stub
                    # would draw along the bbox edge and visually clip the
                    # body. Route those via BFS so they take an L-shape
                    # around the bbox.
                    if side in ("left", "right"):
                        src_cell = to_cell(px, py)
                        rg.allow_pin.add(src_cell)
                        path = rg.lee(src_cell, rail_targets)
                    else:
                        path = None

                    if path:
                        rg.mark_used(path)
                        cell_corners = _polyline_from_path(path)
                        snapped = [
                            (cx_ * GRID_PX, cy_ * GRID_PX)
                            for cx_, cy_ in cell_corners
                        ]
                        if len(snapped) >= 2:
                            first_horiz = snapped[0][1] == snapped[1][1]
                            bridge = (
                                (px, snapped[0][1]) if first_horiz
                                else (snapped[0][0], py)
                            )
                            poly = [(px, py), bridge] + snapped[1:]
                        else:
                            poly = [(px, py), snapped[0] if snapped else (px, rail_y)]
                        # Pin the rail-end exactly to ``rail_y`` (BFS lands
                        # on the trunk row but at grid quantum).
                        last_x, _ = poly[-1]
                        poly[-1] = (last_x, rail_y)
                        # Drop adjacent duplicates (the bridge can coincide
                        # with the pin when the BFS first cell is already
                        # on-axis with the pin) and collinear corners.
                        poly = [
                            pt for i, pt in enumerate(poly)
                            if i == 0 or pt != poly[i - 1]
                        ]
                        j = 1
                        while j < len(poly) - 1:
                            a, b, c = poly[j - 1], poly[j], poly[j + 1]
                            if (a[0] == b[0] == c[0]) or (a[1] == b[1] == c[1]):
                                poly.pop(j)
                            else:
                                j += 1
                        if on_top:
                            poly.reverse()
                    else:
                        # Spine-axis pin (or BFS unreachable): straight stub.
                        if on_top:
                            poly = [(px, rail_y), (px, py)]
                        else:
                            poly = [(px, py), (px, rail_y)]
                        cx0, cy0 = to_cell(px, py)
                        for yy in range(
                            min(cy0, rail_cell_y),
                            max(cy0, rail_cell_y) + 1,
                        ):
                            if 0 <= cx0 < grid_w and 0 <= yy < grid_h:
                                rg.used[cx0][yy] += 1
                    routed_polylines.append((f"net-{net_key}", poly))
                continue

            if len(terms) == 1:
                # A dangling node — nothing to draw.
                continue

            # Multi-terminal mid-net: rectilinear MST + Lee BFS per edge.
            # Order terminals by Manhattan from current centroid; build a
            # tree by repeatedly attaching the nearest unconnected terminal
            # to the existing tree.
            tree_cells: set[tuple[int, int]] = set()
            # Map each tree cell that corresponds to a *pin terminal* to that
            # pin's exact (x, y). Used to snap polyline endpoints back from
            # the routing-grid quantum to the real pin position.
            cell_to_pin: dict[tuple[int, int], tuple[float, float]] = {}
            first_p, first_port = terms[0]
            first_pin_pos = first_p.pin_pos[first_port]
            first_cell = to_cell(*first_pin_pos)
            tree_cells.add(first_cell)
            cell_to_pin[first_cell] = first_pin_pos

            remaining = list(terms[1:])
            while remaining:
                best = None
                best_idx = -1
                best_path: Optional[list[tuple[int, int]]] = None
                for idx, (p, port) in enumerate(remaining):
                    cell = to_cell(*p.pin_pos[port])
                    rg.allow_pin.add(cell)
                    path = rg.lee(cell, tree_cells)
                    if path is None:
                        continue
                    length = len(path)
                    if best is None or length < best:
                        best = length
                        best_idx = idx
                        best_path = path
                if best_path is None:
                    # Could not route — emit a rectilinear fallback so the
                    # Manhattan-only invariant still holds. Try both L-
                    # shapes (elbow at (target.x, src.y) and (src.x,
                    # target.y)) plus a U-shape that detours via the
                    # canvas margin, and pick the one that intersects the
                    # fewest component boxes.
                    p, port = remaining.pop(0)
                    if cell_to_pin:
                        target_xy = next(iter(cell_to_pin.values()))
                    else:
                        trg = next(iter(tree_cells))
                        target_xy = (trg[0] * GRID_PX, trg[1] * GRID_PX)
                    src_xy = p.pin_pos[port]

                    def _box_hits(poly: list[tuple[float, float]]) -> int:
                        hits = 0
                        for i in range(len(poly) - 1):
                            x1, y1 = poly[i]; x2, y2 = poly[i + 1]
                            for pp in placed:
                                bw, bh = pp.desc.bbox_w, pp.desc.bbox_h
                                bx0, by0 = pp.cx - bw / 2, pp.cy - bh / 2
                                bx1, by1 = pp.cx + bw / 2, pp.cy + bh / 2
                                seg_pads = {
                                    pp.pin_pos[port_]
                                    for port_ in pp.pin_pos
                                }
                                if (x1, y1) in seg_pads or (x2, y2) in seg_pads:
                                    continue
                                if x1 == x2:
                                    if bx0 + 0.5 < x1 < bx1 - 0.5:
                                        if max(y1, y2) > by0 + 0.5 and \
                                           min(y1, y2) < by1 - 0.5:
                                            hits += 1
                                elif y1 == y2:
                                    if by0 + 0.5 < y1 < by1 - 0.5:
                                        if max(x1, x2) > bx0 + 0.5 and \
                                           min(x1, x2) < bx1 - 0.5:
                                            hits += 1
                        return hits

                    if src_xy[0] == target_xy[0] or src_xy[1] == target_xy[1]:
                        fallback = [src_xy, target_xy]
                    else:
                        candidates = [
                            [src_xy, (target_xy[0], src_xy[1]), target_xy],
                            [src_xy, (src_xy[0], target_xy[1]), target_xy],
                        ]
                        # Detour via the bottom margin (just above bot rail).
                        detour_y = rail_bot_y - RAIL_GAP / 2
                        candidates.append([
                            src_xy,
                            (src_xy[0], detour_y),
                            (target_xy[0], detour_y),
                            target_xy,
                        ])
                        fallback = min(candidates, key=_box_hits)
                    routed_polylines.append((f"net-{net_key}", fallback))
                    continue

                corners = _polyline_from_path(best_path)
                snapped = [
                    (cx * GRID_PX, cy * GRID_PX) for cx, cy in corners
                ]

                # Bridge the BFS path back to the *exact* pin coordinates.
                # Pins live on the canvas at arbitrary (sub-grid) positions
                # but the BFS only visits grid cells, so the snapped
                # endpoints can sit a few pixels off the actual pin. We
                # insert an orthogonal stub from the pin to the wire's
                # first row/column so the wire meets the pad cleanly.
                src_p, src_port = remaining[best_idx]
                src_pin_pos = src_p.pin_pos[src_port]
                hit_cell = corners[-1]
                tgt_pin_pos = cell_to_pin.get(hit_cell)

                if not snapped:
                    poly_pts: list[tuple[float, float]] = [src_pin_pos]
                elif len(snapped) == 1:
                    end = tgt_pin_pos if tgt_pin_pos is not None else snapped[0]
                    poly_pts = [src_pin_pos, end]
                else:
                    # Source side bridge: the first BFS segment runs in the
                    # axis (corners[0] → corners[1]) — we extend the pin
                    # position perpendicular to that axis to land on the
                    # wire's row / column.
                    first_horizontal = (corners[0][1] == corners[1][1])
                    if first_horizontal:
                        src_bridge = (src_pin_pos[0], snapped[0][1])
                    else:
                        src_bridge = (snapped[0][0], src_pin_pos[1])
                    # Replace ``snapped[0]`` with the bridge — they lie on
                    # the same axis as the next snapped corner, so the
                    # resulting polyline stays orthogonal end-to-end.
                    poly_pts = [src_pin_pos, src_bridge] + snapped[1:]

                    # Target side bridge: only when the hit is a real pin
                    # whose exact coords we know.
                    if tgt_pin_pos is not None and len(corners) >= 2:
                        last_horizontal = (corners[-2][1] == corners[-1][1])
                        if last_horizontal:
                            tgt_bridge = (tgt_pin_pos[0], snapped[-1][1])
                        else:
                            tgt_bridge = (snapped[-1][0], tgt_pin_pos[1])
                        poly_pts.pop()  # drop the snapped last corner
                        poly_pts.append(tgt_bridge)
                        poly_pts.append(tgt_pin_pos)

                # Strip adjacent duplicate points (zero-length segments)
                # and collinear corners — pin-snapping can leave the
                # bridge point coinciding with both the pin and the
                # adjacent BFS corner, which would otherwise render as a
                # spurious "turn" in the polyline counter.
                cleaned: list[tuple[float, float]] = []
                for pt in poly_pts:
                    if cleaned and cleaned[-1] == pt:
                        continue
                    cleaned.append(pt)
                i = 1
                while i < len(cleaned) - 1:
                    px, py = cleaned[i - 1]
                    cx_, cy_ = cleaned[i]
                    nx, ny = cleaned[i + 1]
                    if (cx_ - px, cy_ - py) == (0, 0) or (nx - cx_, ny - cy_) == (0, 0):
                        cleaned.pop(i)
                        continue
                    # Collinear: same axis and same direction sign.
                    same_axis_x = (px == cx_ == nx)
                    same_axis_y = (py == cy_ == ny)
                    if same_axis_x or same_axis_y:
                        cleaned.pop(i)
                        continue
                    i += 1
                routed_polylines.append((f"net-{net_key}", cleaned))
                rg.mark_used(best_path)
                for cell in best_path:
                    tree_cells.add(cell)
                cell_to_pin[corners[0]] = src_pin_pos
                remaining.pop(best_idx)

        polylines = rail_polylines + routed_polylines

        # ---- Solder dots at same-net Steiner T-junctions ----
        # Standard schematic notation: a filled circle at points where 3+
        # wire rays of the same net meet (T or +), to visually distinguish
        # a connection from an unrelated wire crossing.
        solder_dots = _find_solder_dots(routed_polylines)

        # ---- Compact horizontal blanks ----
        # Squeeze x-ranges that hold no vertical content (component bbox or
        # vertical/diagonal wire segment). Horizontal-only spans — including
        # the rails and rung-style wires — automatically shorten because
        # their endpoints sit in occupied ranges and get shifted
        # independently.
        canvas_w = _compact_blanks(placed, polylines, solder_dots, canvas_w)

        # ---- SVG emission ----
        svg = _emit_svg(placed, polylines, canvas_w, canvas_h,
                        rail_top_y, rail_bot_y, top_set, bot_set, uf,
                        glyphs=glyphs, solder_dots=solder_dots,
                        back_annotation=back_annotation)

        _retry_overlap = _segment_overlap_length(polylines)
        if _retry_best_overlap is None or _retry_overlap < _retry_best_overlap:
            _retry_best_overlap = _retry_overlap
            _retry_best_svg = svg
        if _retry_overlap <= _OVERLAP_TOL:
            break

    # Use the lowest-overlap pass when no attempt was clean enough.
    svg = _retry_best_svg if _retry_best_svg is not None else svg

    if filename is not None:
        path = Path(filename)
        if path.suffix.lower() != ".svg":
            path = path.with_suffix(".svg")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(svg)

    return svg


# ---------------------------------------------------------------------------
# Resource loading: per-component-kind SVG glyphs.
#
# The actual SVG parsing/loading lives in ``svg_util``; this module
# only provides domain-specific glue (``_apply_glyphs`` populates the
# ``_CompDesc`` bbox + port_offsets fields from a glyph dict).
# ---------------------------------------------------------------------------
from sycan.svg_util import (
    KIND_GLYPHS as _KIND_GLYPHS,
    load_glyph as _load_glyph_raw,
    load_glyphs as _load_glyphs_raw,
)


def _load_glyph(path: Path) -> Optional[dict]:
    return _load_glyph_raw(path, BOX_W, BOX_H)


def _load_glyphs(
    res_dir: Optional[Union[str, Path]],
) -> dict[str, dict]:
    return _load_glyphs_raw(res_dir, BOX_W, BOX_H)


def _default_port_offsets(d: _CompDesc, w: float, h: float
                          ) -> dict[str, tuple[float, float]]:
    """Pin-tip positions for the default rect placeholder.

    Spine pins extend ``PORT_LEN`` outside the box (top/bottom centre);
    side ports alternate left/right at fixed sub-row offsets.
    Coordinates are relative to the top-left of the box.
    """
    offsets: dict[str, tuple[float, float]] = {}
    offsets[d.spine_top] = (w / 2.0, -PORT_LEN)
    if d.spine_bot != d.spine_top:
        offsets[d.spine_bot] = (w / 2.0, h + PORT_LEN)
    for i, sp in enumerate(d.side_ports):
        left = (i % 2 == 0)
        dy = h / 4.0 + (i // 2) * (h / 2.0)
        if left:
            offsets[sp] = (-PORT_LEN, dy)
        else:
            offsets[sp] = (w + PORT_LEN, dy)
    return offsets


def _apply_glyphs(
    descs: Sequence[_CompDesc],
    glyphs: dict[str, dict],
) -> None:
    """Populate ``bbox_w``/``bbox_h``/``port_offsets`` on each desc.

    A glyph found in ``res/<kind>.svg`` overrides the default rect and
    its canonical port placement. Ports that the glyph doesn't define
    fall back to the canonical fallback positions on the glyph's
    bounding box edge.
    """
    for d in descs:
        glyph = glyphs.get(d.kind)
        if glyph is None:
            d.bbox_w = float(BOX_W)
            d.bbox_h = float(BOX_H)
            d.port_offsets = _default_port_offsets(d, d.bbox_w, d.bbox_h)
            continue

        d.bbox_w = float(glyph["bbox_w"])
        d.bbox_h = float(glyph["bbox_h"])
        gp = glyph["ports"]
        offsets: dict[str, tuple[float, float]] = {}

        # Use marker if present; otherwise fall back to canonical
        # position on the box edge (no PORT_LEN stub — the glyph is
        # expected to draw its own pin lines if it wants them).
        for port in (d.spine_top, d.spine_bot, *d.side_ports):
            if port in gp:
                offsets[port] = gp[port]
                continue
            if port == d.spine_top:
                offsets[port] = (d.bbox_w / 2.0, 0.0)
            elif port == d.spine_bot:
                offsets[port] = (d.bbox_w / 2.0, d.bbox_h)
            else:
                idx = list(d.side_ports).index(port) if port in d.side_ports else 0
                left = (idx % 2 == 0)
                dy = d.bbox_h / 4.0 + (idx // 2) * (d.bbox_h / 2.0)
                offsets[port] = (0.0 if left else d.bbox_w, dy)

        d.port_offsets = offsets


# ---------------------------------------------------------------------------
# SVG writer — defers to svg_util.emit_svg, passing the autodraw
# constants and the port-glyph short-label helper.
# ---------------------------------------------------------------------------
from sycan.svg_util import emit_svg as _svg_util_emit


def _emit_svg(
    placed: Sequence[_Placed],
    polylines: Sequence[tuple[str, list[tuple[float, float]]]],
    canvas_w: float,
    canvas_h: float,
    rail_top_y: float,
    rail_bot_y: float,
    top_set: set[str],
    bot_set: set[str],
    uf: _UF,
    glyphs: Optional[dict[str, dict]] = None,
    solder_dots: Optional[Sequence[tuple[float, float]]] = None,
    back_annotation: Optional[dict[str, Sequence[str]]] = None,
) -> str:
    return _svg_util_emit(
        placed, polylines, canvas_w, canvas_h, rail_top_y, rail_bot_y,
        label_fs=LABEL_FS, port_fs=PORT_FS,
        glyphs=glyphs, short_port=_short,
        solder_dots=solder_dots,
        back_annotation=back_annotation,
    )

