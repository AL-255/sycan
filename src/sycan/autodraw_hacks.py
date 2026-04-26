"""Pattern-detect overrides for ``sycan.autodraw``.

The base autodraw pipeline (SA placer + Lee BFS router) is generic;
it has no concept of higher-level circuit idioms like a "diff-pair
tail" or a "cross-coupled latch". For those, the generic cost function
gives layouts that are technically valid but visually wrong — a
diff-pair tail wire that folds into a U-shape, or a cross-coupled
gate-drain pair routed as two parallel zig-zags instead of the
textbook X.

Every hook in this module is shaped the same way:

1. *Detect* a specific topological pattern in the netlist.
2. *Override* the placement constraint or the routing for that
   pattern, bypassing the generic stage.

These are intentionally pattern-specific (one-shot overrides, not
model improvements). Adding a new idiom = a new detect-then-override
pair here, without touching the generic pipeline.
"""
from __future__ import annotations

import math
from typing import Sequence

from sycan.autodraw import (
    GRID_PX,
    MIN_GAP,
    PORT_LEN,
    _Branch,
    _CompDesc,
    _Placed,
    _UF,
)


# ---------------------------------------------------------------------------
# Spine junctions (e.g. the diff-pair tail).
#
# When two arms terminate at a common spine net and a stem column starts
# from it, the trunk wire wants to run horizontally across the boundary
# between the arm-bottom row and the stem-top row. SA happily collapses
# both pin sets onto the same y, but that lands the trunk on a bbox edge
# and forces the router into a deep U-detour. ``apply_junction_clearance``
# enforces a minimum vertical gap between the two pin sets so the trunk
# always has clear space.
# ---------------------------------------------------------------------------
def detect_spine_junctions(
    branches: Sequence[_Branch],
    uf: _UF,
    spine_index: dict[str, list[tuple[_CompDesc, str]]],
) -> list[dict]:
    """Identify spine-junction nets and the branches meeting at each.

    A junction net has more than two spine endpoints. Branches whose
    *bottom* pin lands there are "above" the junction; branches whose
    *top* pin lands there are "below" it. Junctions where only one
    side is populated are dropped — the trunk has nothing to clear.
    """
    def is_junction(net: str) -> bool:
        return len({id(c) for c, _ in spine_index.get(net, ())}) > 2

    by_net: dict[str, dict] = {}
    for b in branches:
        if not b.descs:
            continue
        last_net = uf.find(b.descs[-1].bot_net())
        if is_junction(last_net):
            entry = by_net.setdefault(
                last_net, {"net": last_net, "above": [], "below": []}
            )
            entry["above"].append(b)
        first_net = uf.find(b.descs[0].top_net())
        if is_junction(first_net):
            entry = by_net.setdefault(
                first_net, {"net": first_net, "above": [], "below": []}
            )
            entry["below"].append(b)
    return [info for info in by_net.values() if info["above"] and info["below"]]


def apply_junction_clearance(
    branches: Sequence[_Branch],
    y_pos: dict[int, float],
    junctions: Sequence[dict],
    min_gap: float,
    sweep_branch,
) -> None:
    """Enforce a ``cross_gap`` between meeting pins at every junction.

    Splits the deficit symmetrically (above branches lift, below
    branches drop) in ``GRID_PX``-aligned increments — sub-grid
    pushes get reverted by the ``snap_up``/``snap_down`` follow-up.
    Touched branches are then re-swept via the caller-provided
    ``sweep_branch(branches_iterable)`` so they stay inside the rail
    bounds.
    """
    if not junctions:
        return

    cross_gap = max(min_gap, 2 * PORT_LEN)
    touched: set[int] = set()
    for j in junctions:
        above = [b for b in j["above"] if b.descs]
        below = [b for b in j["below"] if b.descs]
        if not (above and below):
            continue
        max_above_y = max(
            y_pos[id(b.descs[-1])] + b.descs[-1].bbox_h / 2.0
            for b in above
        )
        min_below_y = min(
            y_pos[id(b.descs[0])] - b.descs[0].bbox_h / 2.0
            for b in below
        )
        deficit = max_above_y + cross_gap - min_below_y
        if deficit <= 0:
            continue
        deficit_grid = math.ceil(deficit / GRID_PX) * GRID_PX
        half_steps = int(deficit_grid // GRID_PX) // 2
        push_up = half_steps * GRID_PX
        push_down = deficit_grid - push_up
        for b in above:
            for d in b.descs:
                y_pos[id(d)] -= push_up
            touched.add(id(b))
        for b in below:
            for d in b.descs:
                y_pos[id(d)] += push_down
            touched.add(id(b))

    if touched:
        sweep_branch(b for b in branches if id(b) in touched)


# ---------------------------------------------------------------------------
# Cross-coupled FET pairs (latches, level shifters, SR flops).
#
# Two FETs whose gates and drains cross-tie produce two nets that the
# generic router lays down as parallel zig-zags. The textbook drawing
# is an X between the two columns — clean, symmetric, with a single
# obvious crossing in the middle. We detect the pattern and emit
# hand-routed Manhattan polylines for the two coupling nets, with the
# crossing point pinned to the column-gap mid-x for symmetry.
# ---------------------------------------------------------------------------
def _segment_enters_bbox(
    p0: tuple[float, float],
    p1: tuple[float, float],
    bbox: tuple[float, float, float, float],
    tol: float = 0.5,
) -> bool:
    """Whether segment ``p0→p1`` passes through the *interior* of ``bbox``.

    ``bbox`` is ``(x_min, y_min, x_max, y_max)``. Shrunk by ``tol`` on each
    side so a segment grazing the edge — typically a wire endpoint sitting
    on a bbox border, like a pin — does not register as a clip.
    """
    x0, y0 = p0
    x1, y1 = p1
    bx0, by0, bx1, by1 = bbox
    bx0 += tol
    by0 += tol
    bx1 -= tol
    by1 -= tol
    if bx0 >= bx1 or by0 >= by1:
        return False
    dx = x1 - x0
    dy = y1 - y0
    t_enter, t_exit = 0.0, 1.0
    for d, p, lo, hi in ((dx, x0, bx0, bx1), (dy, y0, by0, by1)):
        if d == 0:
            if p < lo or p > hi:
                return False
        else:
            t_a = (lo - p) / d
            t_b = (hi - p) / d
            if t_a > t_b:
                t_a, t_b = t_b, t_a
            t_enter = max(t_enter, t_a)
            t_exit = min(t_exit, t_b)
    return t_enter < t_exit


def detect_cross_coupled_descs(
    descs: Sequence[_CompDesc],
    uf: _UF,
) -> list[tuple[_CompDesc, _CompDesc, str, str]]:
    """Topology-only X-pair detector for the SA cost evaluator.

    Same predicate as :func:`detect_cross_coupled_pairs` but operating
    on bare :class:`_CompDesc`s — no placement coordinates required, so
    it can run *before* the SA picks column / mirror state. Returns
    tuples of ``(desc_a, desc_b, net_w1, net_w2)`` where ``net_w1`` is
    the union-find canonical name of the ``a.gate ↔ b.drain`` net and
    ``net_w2`` is ``b.gate ↔ a.drain``. The geometric viability checks
    (gates inward, no third-component clip) are deferred to
    :func:`detect_cross_coupled_pairs` at routing time; here we only
    care that the topology is one the X-router will *try* to realise,
    so the cost function can stop punishing layouts that admit it.
    """
    pairs: list[tuple[_CompDesc, _CompDesc, str, str]] = []
    seen: set[int] = set()
    fets = [
        d for d in descs
        if d.kind in ("nmos", "pmos") and "gate" in d.side_ports
    ]
    for i, a in enumerate(fets):
        if id(a) in seen:
            continue
        a_g = uf.find(a.port_net("gate"))
        a_d = uf.find(a.port_net("drain"))
        for b in fets[i + 1:]:
            if id(b) in seen or b.kind != a.kind:
                continue
            b_g = uf.find(b.port_net("gate"))
            b_d = uf.find(b.port_net("drain"))
            if a_g == b_d and b_g == a_d and a_g != a_d:
                pairs.append((a, b, a_g, b_g))
                seen.add(id(a))
                seen.add(id(b))
                break
    return pairs


def detect_cross_coupled_pairs(
    placed: Sequence[_Placed],
    uf: _UF,
) -> list[tuple[_Placed, _Placed]]:
    """Find FET pairs whose gates cross-couple to each other's drains.

    The classic latch / level-shifter / SR-flop pattern: two NMOS or
    two PMOS where ``M_a.gate == M_b.drain`` and ``M_b.gate ==
    M_a.drain``. Only pairs where the gates face each other (the
    geometry where an X actually fits in the column gap) are
    returned; other layouts fall back to the BFS router.
    """
    pairs: list[tuple[_Placed, _Placed]] = []
    seen: set[int] = set()
    fets = [
        p for p in placed
        if p.desc.kind in ("nmos", "pmos") and "gate" in p.desc.side_ports
    ]
    for i, a in enumerate(fets):
        if id(a) in seen:
            continue
        a_g = uf.find(a.desc.port_net("gate"))
        a_d = uf.find(a.desc.port_net("drain"))
        for b in fets[i + 1:]:
            if id(b) in seen or b.desc.kind != a.desc.kind:
                continue
            b_g = uf.find(b.desc.port_net("gate"))
            b_d = uf.find(b.desc.port_net("drain"))
            if a_g == b_d and b_g == a_d and a_g != a_d:
                a_left = a.cx <= b.cx
                a_g_x = a.pin_pos["gate"][0]
                b_g_x = b.pin_pos["gate"][0]
                a_g_inward = (a_g_x > a.cx) if a_left else (a_g_x < a.cx)
                b_g_inward = (b_g_x < b.cx) if a_left else (b_g_x > b.cx)
                if a_g_inward and b_g_inward:
                    pairs.append((a, b))
                    seen.add(id(a))
                    seen.add(id(b))
                    break
    return pairs


def emit_cross_coupled_x(
    pair: tuple[_Placed, _Placed],
    nets: dict[str, list[tuple[_Placed, str]]],
    uf: _UF,
    placed: Sequence[_Placed],
) -> dict[str, list[list[tuple[float, float]]]]:
    """Hand-route the two cross-coupling nets as a true diagonal X.

    Each arm is a 3-point polyline: ``gate → elbow → drain``, where
    the elbow sits at ``(opposite_gate_x, drain_y)``. That places the
    bottom-left corner of the X at the *left* gate's x and the
    bottom-right corner at the *right* gate's x — the four diagonal
    endpoints all align inside a single ``[left_g.x, right_g.x]``
    rectangle, so the X reads symmetric and each arm has a small
    horizontal stub from the elbow over to the actual drain pin
    (``\\_`` for the left→right arm, ``_/`` for the right→left arm).
    The schematic router otherwise enforces 90° corners; this is the
    one pattern where diagonal routing is allowed because the visual
    gain is unambiguous.

    Bails (returns ``{}``) and lets the BFS take over when:

    * The gates and drains aren't each row-aligned (the X would
      visually skew).
    * The pin x-ordering doesn't support a clean cross.
    * Any segment (diagonal or stub) would clip a *third* component's
      bbox — we'd rather fall back to ugly Manhattan than draw a wire
      through a transistor.

    Extras (third terminals on the coupling net, e.g. ``MN1.drain``
    for the level-shifter ``OUT_P``) are daisy-chained off the drain
    pin via Manhattan stubs.
    """
    a, b = pair
    if a.cx <= b.cx:
        left, right = a, b
    else:
        left, right = b, a

    left_g = left.pin_pos["gate"]
    left_d = left.pin_pos["drain"]
    right_g = right.pin_pos["gate"]
    right_d = right.pin_pos["drain"]

    net_w1 = uf.find(left.desc.port_net("gate"))   # left.gate ↔ right.drain
    net_w2 = uf.find(right.desc.port_net("gate"))  # right.gate ↔ left.drain

    if abs(left_d[1] - right_d[1]) > 0.5:
        return {}
    if abs(left_g[1] - right_g[1]) > 0.5:
        return {}
    if abs(left_g[1] - left_d[1]) < 1e-3:
        return {}
    if left_g[0] >= right_g[0] or left_d[0] >= right_d[0]:
        return {}

    drain_y = left_d[1]
    # Bottom-corner elbows: place the diagonal end of each arm at the
    # *opposite* gate's x. The diagonals then live inside the rectangle
    # spanned by the two gate columns, and the residual horizontal run
    # to the drain pin sits along the FET's bottom edge.
    elbow_w1 = (right_g[0], drain_y)
    elbow_w2 = (left_g[0], drain_y)

    poly_w1 = [left_g, elbow_w1, right_d]
    poly_w2 = [right_g, elbow_w2, left_d]

    pair_ids = {id(left), id(right)}
    for poly in (poly_w1, poly_w2):
        for p0, p1 in zip(poly, poly[1:]):
            for p in placed:
                if id(p) in pair_ids:
                    continue
                bw = p.desc.bbox_w
                bh = p.desc.bbox_h
                bbox = (p.cx - bw / 2.0, p.cy - bh / 2.0,
                        p.cx + bw / 2.0, p.cy + bh / 2.0)
                if _segment_enters_bbox(p0, p1, bbox):
                    return {}

    polys: dict[str, list[list[tuple[float, float]]]] = {
        net_w1: [poly_w1],
        net_w2: [poly_w2],
    }

    drain_y = left_d[1]

    def _add_extras(net_key: str, drain_pin: tuple[float, float]) -> None:
        for p, port in nets.get(net_key, ()):
            if id(p) in pair_ids:
                continue
            ox, oy = p.pin_pos[port]
            if abs(ox - drain_pin[0]) < 0.5:
                polys[net_key].append([drain_pin, (drain_pin[0], oy)])
            else:
                polys[net_key].append([(ox, drain_y), (ox, oy)])
                polys[net_key].append([(ox, drain_y), (drain_pin[0], drain_y)])

    _add_extras(net_w1, right_d)
    _add_extras(net_w2, left_d)

    return polys


def cross_coupled_pinned_polylines(
    placed: Sequence[_Placed],
    nets: dict[str, list[tuple[_Placed, str]]],
    uf: _UF,
) -> dict[str, list[list[tuple[float, float]]]]:
    """One-call API: detect every cross-coupled pair and return the
    union of their hand-routed X polylines, keyed by net.
    """
    out: dict[str, list[list[tuple[float, float]]]] = {}
    for pair in detect_cross_coupled_pairs(placed, uf):
        for net_key, polys in emit_cross_coupled_x(pair, nets, uf, placed).items():
            out.setdefault(net_key, []).extend(polys)
    return out
