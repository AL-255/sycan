"""Drawing-level tests for :func:`sycan.autodraw` with grouped components.

Verifies the visual contract of :meth:`Circuit.group` rendering:
``<rect class="grpbox">`` rectangles enclose every grouped component,
nested groups produce concentric rectangles, multiple disjoint groups
each get their own box, group labels carry the dotted hierarchy path,
and the standard wire-crossing / structural assertions still hold.

Each test writes an SVG under ``tests/drawing/diagrams/`` so a human
can spot-check the result in a browser. File names continue the same
numbered series as ``test_autodraw.py``.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from sycan import Circuit, autodraw, cas

from .test_autodraw import (
    _column_x_of,
    _common_assertions,
    _components,
    _pads,
    _save,
)


DIAGRAM_DIR = Path(__file__).parent / "diagrams"
DIAGRAM_DIR.mkdir(exist_ok=True)


# Regex matching every group rectangle plus its trailing label.
_GROUP_RECT_RE = re.compile(
    r'<rect class="grpbox" x="([-\d.]+)" y="([-\d.]+)" '
    r'width="([-\d.]+)" height="([-\d.]+)" rx="4" ry="4"/>'
)
_GROUP_LABEL_RE = re.compile(
    r'<text class="grplab" x="([-\d.]+)" y="([-\d.]+)">([^<]+)</text>'
)
# Capture a rectangle immediately followed by its label so the pairing
# is unambiguous regardless of bounding-box overlap. ``\s*`` allows the
# whitespace/newline that the writer emits between the two elements.
_GROUP_PAIR_RE = re.compile(
    r'<rect class="grpbox" x="([-\d.]+)" y="([-\d.]+)" '
    r'width="([-\d.]+)" height="([-\d.]+)" rx="4" ry="4"/>\s*'
    r'<text class="grplab" x="[-\d.]+" y="[-\d.]+">([^<]+)</text>'
)


def _group_boxes(svg: str) -> list[tuple[float, float, float, float]]:
    """Return ``(x, y, w, h)`` for every ``<rect class="grpbox">``."""
    return [
        (float(x), float(y), float(w), float(h))
        for x, y, w, h in _GROUP_RECT_RE.findall(svg)
    ]


def _group_labels(svg: str) -> list[tuple[float, float, str]]:
    """Return ``(x, y, text)`` for every ``<text class="grplab">``."""
    return [
        (float(x), float(y), label)
        for x, y, label in _GROUP_LABEL_RE.findall(svg)
    ]


def _labelled_group_boxes(
    svg: str,
) -> dict[str, tuple[float, float, float, float]]:
    """Return ``{label: (x, y, w, h)}`` by pairing rects with their
    inline labels in document order — robust to overlapping boxes
    where label-x alone wouldn't disambiguate."""
    out: dict[str, tuple[float, float, float, float]] = {}
    for x, y, w, h, label in _GROUP_PAIR_RE.findall(svg):
        out[label] = (float(x), float(y), float(w), float(h))
    return out


def _comp_center(svg: str, name: str) -> tuple[float, float]:
    """Return ``(cx, cy)`` for the named component box."""
    for _kind, n, x, y, w, h in _components(svg):
        if n == name:
            return (x + w / 2.0, y + h / 2.0)
    raise AssertionError(f"component {name!r} not in SVG")


def _rect_contains(rect, x, y, eps: float = 0.5) -> bool:
    rx, ry, rw, rh = rect
    return (rx - eps) <= x <= (rx + rw + eps) and \
           (ry - eps) <= y <= (ry + rh + eps)


def _rect_area(rect) -> float:
    _x, _y, w, h = rect
    return w * h


# ---------------------------------------------------------------------------
# Test G1 — Simple two-resistor divider grouped into one subcircuit.
# Expect: exactly one grpbox; both grouped R's inside it; ungrouped
# V1 outside.
# ---------------------------------------------------------------------------

def _build_simple_grouped_divider() -> Circuit:
    c = Circuit("grouped_divider")
    c.add_vsource("V1", "VDD", "0", 5)
    R1 = c.add_resistor("R1", "VDD", "mid", 1000)
    R2 = c.add_resistor("R2", "mid", "0", 1000)
    c.group([R1, R2], name="DIV")
    return c


def test_group_box_around_simple_divider():
    c = _build_simple_grouped_divider()
    svg = autodraw(c)
    _save("20_grouped_divider", svg)

    _common_assertions(svg, {"V1", "DIV.R1", "DIV.R2"})

    boxes = _group_boxes(svg)
    labels = _group_labels(svg)
    assert len(boxes) == 1, f"expected 1 group rect, got {len(boxes)}"
    assert any(text == "DIV" for *_, text in labels), \
        f"missing DIV label in {labels!r}"

    rect = boxes[0]
    r1c = _comp_center(svg, "DIV.R1")
    r2c = _comp_center(svg, "DIV.R2")
    assert _rect_contains(rect, *r1c)
    assert _rect_contains(rect, *r2c)

    # Ungrouped V1 must not be inside the group rectangle.
    v1c = _comp_center(svg, "V1")
    assert not _rect_contains(rect, *v1c), \
        "ungrouped V1 should sit outside the DIV bounding box"


# ---------------------------------------------------------------------------
# Test G2 — Nested groups: an outer wrapper containing an inner divider
# subcircuit. Expect two concentric rectangles, both labelled correctly,
# with the inner rectangle strictly inside the outer.
# ---------------------------------------------------------------------------

def _build_nested_groups() -> Circuit:
    # Inner: two resistors form a divider.
    inner_body = Circuit("INNER_DIV")
    inner_body.add_resistor("Ra", "in", "mid", 1000)
    inner_body.add_resistor("Rb", "mid", "out", 1000)

    # Outer: instantiates the inner divider plus its own load resistor.
    outer_body = Circuit("OUTER")
    outer_body.add_subcircuit(
        "Y1", inner_body, port_map={"in": "p", "out": "q"},
    )
    outer_body.add_resistor("Rload", "q", "0", 1000)

    top = Circuit("nested_groups")
    top.add_vsource("V1", "p", "0", 5)
    top.add_subcircuit("X1", outer_body, port_map={"p": "p", "q": "q"})
    return top


def test_nested_groups_render_concentric_boxes():
    c = _build_nested_groups()
    svg = autodraw(c)
    _save("21_nested_groups", svg)

    _common_assertions(svg, {"V1", "X1.Y1.Ra", "X1.Y1.Rb", "X1.Rload"})

    boxes = _group_boxes(svg)
    labels = {text for *_, text in _group_labels(svg)}

    # Expected nesting: X1 (outer) and X1.Y1 (inner).
    assert "X1" in labels
    assert "X1.Y1" in labels
    assert len(boxes) == 2

    outer = max(boxes, key=_rect_area)
    inner = min(boxes, key=_rect_area)
    assert outer is not inner

    # Inner rectangle must be entirely inside the outer one.
    ix, iy, iw, ih = inner
    assert _rect_contains(outer, ix, iy)
    assert _rect_contains(outer, ix + iw, iy + ih)

    # All four leaves of the outer group sit inside the outer rect.
    for leaf in ("X1.Y1.Ra", "X1.Y1.Rb", "X1.Rload"):
        cx, cy = _comp_center(svg, leaf)
        assert _rect_contains(outer, cx, cy), \
            f"{leaf} centre not in outer X1 box"

    # The two inner-divider resistors sit inside the inner rect; Rload
    # belongs to X1 but not to X1.Y1, so it must sit outside it.
    for leaf in ("X1.Y1.Ra", "X1.Y1.Rb"):
        cx, cy = _comp_center(svg, leaf)
        assert _rect_contains(inner, cx, cy), \
            f"{leaf} centre not in inner X1.Y1 box"
    rload_c = _comp_center(svg, "X1.Rload")
    assert not _rect_contains(inner, *rload_c), \
        "Rload belongs to X1 but is being drawn inside X1.Y1"


# ---------------------------------------------------------------------------
# Test G3 — Two disjoint groups in the same circuit. Each gets its own
# bounding box; the boxes don't share components.
# ---------------------------------------------------------------------------

def _build_two_groups() -> Circuit:
    c = Circuit("two_groups")
    c.add_vsource("V1", "n_a", "0", 5)
    A1 = c.add_resistor("A1", "n_a", "n_ab", 1000)
    A2 = c.add_resistor("A2", "n_ab", "n_b", 1000)
    B1 = c.add_resistor("B1", "n_b", "n_bc", 1000)
    B2 = c.add_resistor("B2", "n_bc", "0", 1000)
    c.group([A1, A2], name="GA")
    c.group([B1, B2], name="GB")
    return c


def test_two_disjoint_groups_get_separate_boxes():
    c = _build_two_groups()
    svg = autodraw(c)
    _save("22_two_disjoint_groups", svg)

    _common_assertions(svg, {"V1", "GA.A1", "GA.A2", "GB.B1", "GB.B2"})

    boxes = _labelled_group_boxes(svg)
    assert "GA" in boxes and "GB" in boxes, \
        f"expected both group rects, got {sorted(boxes)!r}"
    assert len(boxes) == 2

    # Membership: each box contains its members only.
    members = {
        "GA": ("GA.A1", "GA.A2"),
        "GB": ("GB.B1", "GB.B2"),
    }
    for label, comp_names in members.items():
        rect = boxes[label]
        for nm in comp_names:
            cx, cy = _comp_center(svg, nm)
            assert _rect_contains(rect, cx, cy), \
                f"{nm} not inside {label} box {rect!r}"


# ---------------------------------------------------------------------------
# Test G4 — Grouping wraps a feedback amplifier (Ri, Rf, opamp). The
# box should enclose every grouped leaf, including the OPAMP's flattened
# VCVS innards which carry a deeper group_path.
# ---------------------------------------------------------------------------

def _build_grouped_amp() -> Circuit:
    c = Circuit("grouped_inv_amp")
    c.add_vsource("V1", "in", "0", 1)
    Ri = c.add_resistor("Ri", "in", "inv", 1000)
    Rf = c.add_resistor("Rf", "out", "inv", 10000)
    U1 = c.add_opamp("U1", "0", "inv", "out")
    c.add_resistor("Rl", "out", "0", 1000)
    c.group([Ri, Rf, U1], name="IAMP")
    return c


def test_group_box_around_grouped_inverting_amp():
    c = _build_grouped_amp()
    svg = autodraw(c)
    _save("23_grouped_inverting_amp", svg)

    # Note: OPAMP expands to two VCVS leaves; group_path tagging makes
    # both X1.U1 children show up in flat_components.
    expected = {"V1", "IAMP.Ri", "IAMP.Rf", "IAMP.U1.E1", "IAMP.U1.Eout", "Rl"}
    _common_assertions(svg, expected)

    boxes = _labelled_group_boxes(svg)
    # IAMP outer plus IAMP.U1 inner (from the built-in OPAMP body).
    assert "IAMP" in boxes
    assert "IAMP.U1" in boxes

    outer = boxes["IAMP"]
    inner = boxes["IAMP.U1"]
    # Inner box (the OPAMP) lives inside the outer IAMP box because the
    # OPAMP is a member of the IAMP group.
    ix, iy, iw, ih = inner
    assert _rect_contains(outer, ix, iy)
    assert _rect_contains(outer, ix + iw, iy + ih)

    # Every grouped leaf must sit inside the outer IAMP box.
    for leaf in ("IAMP.Ri", "IAMP.Rf", "IAMP.U1.E1", "IAMP.U1.Eout"):
        cx, cy = _comp_center(svg, leaf)
        assert _rect_contains(outer, cx, cy), \
            f"grouped leaf {leaf} sits outside the IAMP box"

    # The OPAMP's two VCVS leaves must sit inside the inner IAMP.U1 box.
    for leaf in ("IAMP.U1.E1", "IAMP.U1.Eout"):
        cx, cy = _comp_center(svg, leaf)
        assert _rect_contains(inner, cx, cy), \
            f"{leaf} not inside its own OPAMP box"
    # …whereas Ri and Rf belong only to the outer group, so they must
    # NOT sit inside the inner OPAMP box.
    for leaf in ("IAMP.Ri", "IAMP.Rf"):
        cx, cy = _comp_center(svg, leaf)
        assert not _rect_contains(inner, cx, cy), \
            f"{leaf} (outer-only) is inside the inner OPAMP box"


# ---------------------------------------------------------------------------
# Test G5 — Group label sits near the top-left of its rectangle.
# ---------------------------------------------------------------------------

def test_group_label_is_anchored_top_left():
    c = _build_simple_grouped_divider()
    svg = autodraw(c)

    boxes = _group_boxes(svg)
    labels = _group_labels(svg)
    assert len(boxes) == 1 and len(labels) == 1
    rect = boxes[0]
    lx, ly, text = labels[0]
    assert text == "DIV"
    # Label is inset by 4 px from the rectangle's left edge.
    assert abs(lx - (rect[0] + 4)) < 0.5
    # Label baseline sits a bit below the rectangle's top edge.
    assert rect[1] < ly < rect[1] + 30


# ---------------------------------------------------------------------------
# Test G6 — A circuit with no groups must not emit any grpbox element.
# Guards against accidental rectangles for top-level components.
# ---------------------------------------------------------------------------

def test_ungrouped_circuit_emits_no_grpbox_rect():
    c = Circuit("flat")
    c.add_vsource("V1", "VDD", "0", 5)
    c.add_resistor("R1", "VDD", "mid", 1000)
    c.add_resistor("R2", "mid", "0", 1000)
    svg = autodraw(c)
    assert _group_boxes(svg) == []
    # Built-in OPAMP (no .group call) also must not produce a group box
    # — its hierarchy is invisible to autodraw unless a user grouped it.
    c2 = Circuit("flat_with_opamp")
    c2.add_vsource("V1", "in", "0", 1)
    c2.add_resistor("Ri", "in", "inv", 1000)
    c2.add_resistor("Rf", "out", "inv", 10000)
    c2.add_opamp("U1", "0", "inv", "out")
    c2.add_resistor("Rl", "out", "0", 1000)
    svg2 = autodraw(c2)
    boxes2 = _group_boxes(svg2)
    # OPAMP's body is a SubCircuit, so its two VCVS leaves DO carry
    # group_path == ("U1",) and produce one rectangle. Confirm that
    # rectangle is labelled "U1" — the user can still see the
    # hierarchy that the OPAMP block injects.
    assert len(boxes2) == 1
    label_texts = {text for *_, text in _group_labels(svg2)}
    assert label_texts == {"U1"}


# ---------------------------------------------------------------------------
# Test G7 — Group cohesion biases SA toward adjacent columns. Build a
# circuit where two grouped resistors otherwise sit in non-adjacent
# columns; confirm they end up in the same or neighbouring columns
# after optimization.
# ---------------------------------------------------------------------------

def test_group_cohesion_keeps_members_in_neighbouring_columns():
    c = Circuit("cohesion")
    # Three independent vertical branches, two of which we'll group.
    c.add_vsource("V1", "VDD", "0", 5)
    R1 = c.add_resistor("R1", "VDD", "a", 1000)
    c.add_resistor("RA", "a", "0", 1000)
    R2 = c.add_resistor("R2", "VDD", "b", 1000)
    c.add_resistor("RB", "b", "0", 1000)
    c.add_resistor("Rmid", "VDD", "c", 1000)
    c.add_resistor("Rmid2", "c", "0", 1000)
    # Group the two non-adjacent stacks' top resistors.
    c.group([R1, R2], name="G")

    svg = autodraw(c, seed=42)
    _save("24_group_cohesion", svg)

    pads = _pads(svg)
    name_to_col = {}
    for comp in _components(svg):
        _kind, name, x, y, w, h = comp
        name_to_col[name] = _column_x_of(comp, pads)

    # The two grouped resistors should be in adjacent columns (column
    # spacing is COL_W + glyph widths; allow up to ~2× the typical pitch
    # to call them "near"). The exact spacing depends on glyph sizes;
    # using 2× MIN_PITCH as a generous upper bound.
    from sycan.autodraw import MIN_PITCH
    spread = abs(name_to_col["G.R1"] - name_to_col["G.R2"])
    assert spread <= 2 * MIN_PITCH, (
        f"grouped components landed {spread:.1f}px apart (limit "
        f"{2 * MIN_PITCH:.1f}); SA cohesion not biting"
    )


# ---------------------------------------------------------------------------
# Test G8 — Group rectangles render BEFORE wires/components so they sit
# in the background. Verify by their position in the SVG byte stream.
# ---------------------------------------------------------------------------

def test_group_box_renders_behind_other_primitives():
    c = _build_simple_grouped_divider()
    svg = autodraw(c)

    rect_pos = svg.find('<rect class="grpbox"')
    wire_pos = svg.find('<polyline class="wire"')
    rail_pos = svg.find('<polyline class="rail"')
    # At least one of the wire/rail lines exists.
    other = [p for p in (wire_pos, rail_pos) if p >= 0]
    assert other, "expected some wire or rail to be in the SVG"
    assert rect_pos >= 0
    assert rect_pos < min(other), (
        "group rect should appear before any wire / rail so it sits "
        "behind them in z-order"
    )


# ---------------------------------------------------------------------------
# Test G9 — Sanity: a circuit with a group still satisfies the standard
# wire-doesn't-cross-component invariant.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Test G10 — Each nesting level adds visible padding. An outer rectangle
# whose members are exactly the inner rectangle's members (degenerate
# case: outer body just instantiates one inner sub) must still extend
# past the inner one on every side, by the configured depth step.
# ---------------------------------------------------------------------------

def test_outer_group_pads_outwards_from_inner_group():
    inner_body = Circuit("INNER")
    inner_body.add_resistor("R1", "p", "q", 1000)
    inner_body.add_resistor("R2", "q", "r", 1000)

    # The outer body's only content is a single instance of inner — the
    # leaves of the outer group are the leaves of the inner group, so
    # absent the depth-step the two rectangles would coincide.
    outer_body = Circuit("OUTER")
    outer_body.add_subcircuit(
        "Y1", inner_body, port_map={"p": "p", "r": "r"},
    )

    c = Circuit("pad_test")
    c.add_vsource("V1", "p", "0", 5)
    c.add_subcircuit("X1", outer_body, port_map={"p": "p", "r": "r"})
    c.add_resistor("Rl", "r", "0", 1000)

    svg = autodraw(c)
    _save("25_outer_pads_outwards", svg)

    boxes = _labelled_group_boxes(svg)
    assert "X1" in boxes and "X1.Y1" in boxes

    ox, oy, ow, oh = boxes["X1"]
    ix, iy, iw, ih = boxes["X1.Y1"]

    # The outer rectangle must extend strictly past the inner one on
    # all four sides — that's the depth-step padding working.
    assert ox < ix, f"outer left edge {ox} should be left of inner {ix}"
    assert oy < iy, f"outer top edge {oy} should be above inner {iy}"
    assert (ox + ow) > (ix + iw), \
        f"outer right edge {ox + ow} should be right of inner {ix + iw}"
    assert (oy + oh) > (iy + ih), \
        f"outer bottom edge {oy + oh} should be below inner {iy + ih}"

    # And the extra padding should match the configured step (allow a
    # 1-px tolerance for float rounding in the SVG output).
    from sycan.autodraw import _GROUP_DEPTH_STEP
    assert abs((ix - ox) - _GROUP_DEPTH_STEP) < 1.0
    assert abs((iy - oy) - _GROUP_DEPTH_STEP) < 1.0
    assert abs(((ox + ow) - (ix + iw)) - _GROUP_DEPTH_STEP) < 1.0
    assert abs(((oy + oh) - (iy + ih)) - _GROUP_DEPTH_STEP) < 1.0


# ---------------------------------------------------------------------------
# Test G11 — Three levels of nesting → margins grow monotonically from
# innermost to outermost. Confirms the per-level step is cumulative.
# ---------------------------------------------------------------------------

def test_three_level_nesting_margins_grow_monotonically():
    innermost = Circuit("INNERMOST")
    innermost.add_resistor("R", "a", "b", 1000)

    middle = Circuit("MIDDLE")
    middle.add_subcircuit("Z1", innermost, port_map={"a": "a", "b": "b"})

    outer = Circuit("OUTER")
    outer.add_subcircuit("Y1", middle, port_map={"a": "a", "b": "b"})

    c = Circuit("deep_pad")
    c.add_vsource("V1", "a", "0", 5)
    c.add_subcircuit("X1", outer, port_map={"a": "a", "b": "b"})
    c.add_resistor("Rl", "b", "0", 1000)

    svg = autodraw(c)
    _save("26_three_level_nesting", svg)

    boxes = _labelled_group_boxes(svg)
    for label in ("X1", "X1.Y1", "X1.Y1.Z1"):
        assert label in boxes, f"missing {label}"

    # All three rectangles share the same single member resistor, so
    # padding alone differentiates them. Widths should grow strictly
    # from innermost (X1.Y1.Z1) outwards.
    inner_w = boxes["X1.Y1.Z1"][2]
    middle_w = boxes["X1.Y1"][2]
    outer_w = boxes["X1"][2]
    assert inner_w < middle_w < outer_w, (
        f"expected strictly growing widths inner→outer, "
        f"got {inner_w} < {middle_w} < {outer_w}"
    )

    # Each level should add exactly the configured step on each side
    # (so the diff between consecutive widths is 2 × step).
    from sycan.autodraw import _GROUP_DEPTH_STEP
    assert abs((middle_w - inner_w) - 2 * _GROUP_DEPTH_STEP) < 1.0
    assert abs((outer_w - middle_w) - 2 * _GROUP_DEPTH_STEP) < 1.0


# ---------------------------------------------------------------------------
# Test G12 — Group box must not overlap a neighbouring (ungrouped)
# component's bbox. SA + per-column padding should keep enough horizontal
# clearance so the dashed rectangle has its own real estate.
# ---------------------------------------------------------------------------

def test_group_box_does_not_overlap_neighbour_component():
    c = Circuit("clearance")
    # Single grouped column on the left, ungrouped column on the right.
    c.add_vsource("V1", "VDD", "0", 5)
    R1 = c.add_resistor("R1", "VDD", "ga", 1000)
    R2 = c.add_resistor("R2", "ga", "0", 1000)
    c.group([R1, R2], name="G")
    c.add_resistor("Rext", "VDD", "0", 1000)

    svg = autodraw(c)
    _save("27_group_clearance", svg)

    boxes = _labelled_group_boxes(svg)
    assert "G" in boxes
    gx, gy, gw, gh = boxes["G"]

    # Find the un-grouped component and confirm it sits strictly
    # outside the group rectangle on every side.
    for kind, name, x, y, w, h in _components(svg):
        if name != "Rext":
            continue
        # The non-group component's bbox must not overlap the group rect.
        no_overlap = (
            x + w <= gx
            or x >= gx + gw
            or y + h <= gy
            or y >= gy + gh
        )
        assert no_overlap, (
            f"ungrouped {name} bbox ({x},{y},{w},{h}) overlaps "
            f"group G rect ({gx},{gy},{gw},{gh})"
        )
        break
    else:
        pytest.fail("Rext component not found in SVG")


# ---------------------------------------------------------------------------
# Test G13 — Deeper hierarchy → wider columns. With several nested levels,
# the column carrying the grouped components must be visibly wider than
# the same circuit drawn flat.
# ---------------------------------------------------------------------------

def test_deep_grouping_widens_column():
    def _comp_x(svg: str, comp_name: str) -> float:
        for _kind, name, x, y, w, h in _components(svg):
            if name == comp_name:
                return x
        raise AssertionError(f"{comp_name!r} not in SVG")

    # Three independent rail-to-rail branches so each lands in its own
    # column. The middle branch will be the one we group / nest.
    flat = Circuit("flat")
    flat.add_vsource("V1", "VDD", "0", 5)
    flat.add_resistor("Rmid", "VDD", "0", 1000)
    flat.add_resistor("Rext", "VDD", "0", 1000)
    flat_svg = autodraw(flat, optimize=False)
    flat_v1 = _comp_x(flat_svg, "V1")
    flat_rext = _comp_x(flat_svg, "Rext")
    flat_span = abs(flat_rext - flat_v1)

    # Same circuit but Rmid is wrapped in two nested groups. The middle
    # column now reserves padding for both group rectangles, so V1 and
    # Rext should sit visibly farther apart than in the flat layout.
    inner_body = Circuit("INNER")
    inner_body.add_resistor("Rmid", "VDD", "0", 1000)
    outer_body = Circuit("OUTER")
    outer_body.add_subcircuit(
        "Y1", inner_body, port_map={"VDD": "VDD"},
    )
    nested = Circuit("nested")
    nested.add_vsource("V1", "VDD", "0", 5)
    nested.add_subcircuit(
        "X1", outer_body, port_map={"VDD": "VDD"},
    )
    nested.add_resistor("Rext", "VDD", "0", 1000)
    nested_svg = autodraw(nested, optimize=False)
    nested_v1 = _comp_x(nested_svg, "V1")
    nested_rext = _comp_x(nested_svg, "Rext")
    nested_span = abs(nested_rext - nested_v1)

    assert nested_span > flat_span, (
        f"nested span V1↔Rext = {nested_span}; flat span = {flat_span}; "
        "expected nested to be wider once two group margins are reserved"
    )


# ---------------------------------------------------------------------------
# Test G14 — collapse: top-level group renders as a single purple-outlined
# rectangle and the inner leaves are absent from the SVG.
# ---------------------------------------------------------------------------

_COLLAPSED_RE = re.compile(
    r'<rect class="comp collapsed" data-comp="collapsed" '
    r'data-name="([^"]+)" x="([-\d.]+)" y="([-\d.]+)" '
    r'width="([-\d.]+)" height="([-\d.]+)"'
)


def _collapsed_rects(svg: str) -> list[tuple[str, float, float, float, float]]:
    return [
        (name, float(x), float(y), float(w), float(h))
        for name, x, y, w, h in _COLLAPSED_RE.findall(svg)
    ]


def test_collapse_top_level_group_renders_as_single_placeholder():
    body = Circuit("AMP")
    body.add_resistor("Ri", "in", "inv", 1000)
    body.add_resistor("Rf", "out", "inv", 10000)
    body.add_opamp("U1", "0", "inv", "out")

    c = Circuit("top")
    c.add_vsource("V1", "in", "0", 1)
    c.add_subcircuit("X1", body, port_map={"in": "in", "out": "out"})
    c.add_resistor("Rl", "out", "0", 1000)

    svg = autodraw(c, collapse="X1")
    _save("28_collapse_top", svg)

    # Exactly one collapsed placeholder, named after the group path.
    placeholders = _collapsed_rects(svg)
    assert len(placeholders) == 1
    name, _x, _y, _w, _h = placeholders[0]
    assert name == "X1"

    # The inner leaves of X1 must be GONE from the rendered SVG.
    rendered = {n for _kind, n, *_ in _components(svg)}
    for hidden in ("X1.Ri", "X1.Rf", "X1.U1.E1", "X1.U1.Eout"):
        assert hidden not in rendered, \
            f"collapsed leaf {hidden} should not appear in SVG"

    # The non-collapsed surroundings should still be there.
    assert "V1" in rendered
    assert "Rl" in rendered

    # CSS class for purple stroke must be defined exactly once.
    assert ".collapsed{" in svg
    assert "stroke:#7a36a6" in svg


# ---------------------------------------------------------------------------
# Test G15 — Collapsing only the inner SubCircuit keeps the outer group
# expanded but hides the inner one. Outer bounding box still wraps the
# placeholder.
# ---------------------------------------------------------------------------

def test_collapse_only_inner_group():
    inner_body = Circuit("INNER")
    inner_body.add_resistor("Ra", "in", "mid", 1000)
    inner_body.add_resistor("Rb", "mid", "out", 1000)

    outer_body = Circuit("OUTER")
    outer_body.add_subcircuit(
        "Y1", inner_body, port_map={"in": "p", "out": "q"},
    )
    outer_body.add_resistor("Rload", "q", "0", 1000)

    c = Circuit("top")
    c.add_vsource("V1", "p", "0", 5)
    c.add_subcircuit("X1", outer_body, port_map={"p": "p", "q": "q"})

    svg = autodraw(c, collapse="X1.Y1")
    _save("29_collapse_inner_only", svg)

    placeholders = _collapsed_rects(svg)
    assert len(placeholders) == 1
    assert placeholders[0][0] == "X1.Y1"

    rendered = {n for _kind, n, *_ in _components(svg)}
    # Inner leaves are hidden.
    assert "X1.Y1.Ra" not in rendered
    assert "X1.Y1.Rb" not in rendered
    # Outer-group sibling still drawn.
    assert "X1.Rload" in rendered

    # Outer X1 group bounding box must still appear (since X1 itself
    # wasn't collapsed) and must enclose the inner placeholder.
    boxes = _labelled_group_boxes(svg)
    assert "X1" in boxes
    ox, oy, ow, oh = boxes["X1"]
    name, px, py, pw, ph = placeholders[0]
    px_c, py_c = px + pw / 2, py + ph / 2
    assert _rect_contains((ox, oy, ow, oh), px_c, py_c)


# ---------------------------------------------------------------------------
# Test G16 — collapse=None (the default) leaves the SVG identical to a
# non-collapsing call. Sanity check that the new code path is opt-in.
# ---------------------------------------------------------------------------

def test_collapse_none_is_default_behaviour():
    c = _build_simple_grouped_divider()
    svg_default = autodraw(c, optimize=False, seed=0)
    svg_explicit = autodraw(c, optimize=False, seed=0, collapse=None)
    assert svg_default == svg_explicit


# ---------------------------------------------------------------------------
# Test G17 — multiple collapse targets in a single call. List form works.
# ---------------------------------------------------------------------------

def test_collapse_accepts_list_of_paths():
    a_body = Circuit("ABODY")
    a_body.add_resistor("Ra", "in", "out", 1000)
    b_body = Circuit("BBODY")
    b_body.add_resistor("Rb", "in", "out", 1000)

    c = Circuit("top")
    c.add_vsource("V1", "n", "0", 1)
    c.add_subcircuit("XA", a_body, port_map={"in": "n", "out": "n"})
    c.add_subcircuit("XB", b_body, port_map={"in": "n", "out": "n"})

    svg = autodraw(c, collapse=["XA", "XB"])
    _save("30_collapse_two_targets", svg)

    names = {name for name, *_ in _collapsed_rects(svg)}
    assert names == {"XA", "XB"}

    rendered = {n for _kind, n, *_ in _components(svg)}
    assert "XA.Ra" not in rendered
    assert "XB.Rb" not in rendered


# ---------------------------------------------------------------------------
# Test G18 — invalid collapse path raises a clear ValueError listing
# the actual paths.
# ---------------------------------------------------------------------------

def test_collapse_invalid_path_raises():
    body = Circuit("BODY")
    body.add_resistor("R", "a", "b", 1)
    c = Circuit("top")
    c.add_subcircuit("X1", body, port_map={"a": "a", "b": "b"})

    with pytest.raises(ValueError, match="not found in hierarchy"):
        autodraw(c, collapse="X9")
    # Subset-correct error: misspelling a deeper level too.
    with pytest.raises(ValueError, match="not found in hierarchy"):
        autodraw(c, collapse="X1.Whatever")


# ---------------------------------------------------------------------------
# Test G19 — the collapsed placeholder still has a pin per external
# connection of the original SubCircuit, and those pins route to wires
# that connect to the parent context (V1, Rl).
# ---------------------------------------------------------------------------

def test_collapsed_placeholder_exposes_external_pins():
    body = Circuit("AMP")
    body.add_resistor("Ri", "in", "out", 1000)

    c = Circuit("top")
    c.add_vsource("V1", "in", "0", 1)
    c.add_subcircuit("X1", body, port_map={"in": "in", "out": "out"})
    c.add_resistor("Rl", "out", "0", 1000)

    svg = autodraw(c, collapse="X1")
    _save("31_collapse_pins", svg)

    placeholders = _collapsed_rects(svg)
    assert len(placeholders) == 1
    _name, x, y, w, h = placeholders[0]

    # The placeholder's pin pads sit on stubs that extend a fixed
    # distance outside the rect. Count pads inside an enlarged bbox
    # (rect + PORT_LEN on each side); for a 2-pin SubCircuit there
    # must be at least 2 pads attached to those spine stubs.
    from sycan.autodraw import PORT_LEN
    pads = _pads(svg)
    pad = PORT_LEN + 2
    on_box = [
        (px, py) for px, py in pads
        if x - pad <= px <= x + w + pad
        and y - pad <= py <= y + h + pad
    ]
    assert len(on_box) >= 2, (
        f"expected ≥2 pin pads on/around the X1 placeholder, got {len(on_box)}"
    )


# ---------------------------------------------------------------------------
# Test G20 — collapse works when given via the SPICE-string form too:
# the autodraw() entry parses the netlist itself.
# ---------------------------------------------------------------------------

def test_collapse_works_with_spice_input():
    netlist = """divider via .SUBCKT then collapsed
.SUBCKT DIV in out
R1 in mid 1k
R2 mid out 1k
.ENDS
V1 vdd 0 5
X1 vdd tap DIV
W1 tap 0
.end
"""
    svg = autodraw(netlist, collapse="X1")
    placeholders = _collapsed_rects(svg)
    assert len(placeholders) == 1
    assert placeholders[0][0] == "X1"
    rendered = {n for _kind, n, *_ in _components(svg)}
    assert "X1.R1" not in rendered
    assert "X1.R2" not in rendered


@pytest.mark.parametrize("builder", [
    _build_simple_grouped_divider,
    _build_nested_groups,
    _build_two_groups,
    _build_grouped_amp,
])
def test_grouped_circuits_obey_no_wire_crossing(builder):
    svg = autodraw(builder())
    # ``_common_assertions`` already runs ``_no_wire_crosses_component``.
    # Re-run it here in isolation so the parameterization makes the
    # failing case obvious.
    from .test_autodraw import _no_wire_crosses_component
    ok, why = _no_wire_crosses_component(svg)
    assert ok, why
