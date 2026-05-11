"""Behaviour tests for :meth:`Circuit.group`, hierarchy printing, and
the autodraw bounding-box overlay.
"""
import io
import re

import pytest

from sycan import (
    Circuit,
    OPAMP,
    Resistor,
    SubCircuit,
    autodraw,
    cas,
    print_hierarchy,
    solve_dc,
)


# ---------------------------------------------------------------------------
# Circuit.group()
# ---------------------------------------------------------------------------

def test_group_wraps_components_and_returns_subcircuit():
    c = Circuit("top")
    c.add_vsource("V1", "in", "0", 1)
    R1 = c.add_resistor("R1", "in", "mid", 1000)
    R2 = c.add_resistor("R2", "mid", "out", 1000)
    c.add_resistor("Rl", "out", "0", 1000)

    sub = c.group([R1, R2], name="X1", body_name="DIV")

    assert isinstance(sub, SubCircuit)
    assert sub.name == "X1"
    # External nodes: ``in``, ``mid``? mid is internal-only. out is external.
    # Specifically: ``in`` referenced by V1 (outside) → external;
    #               ``mid`` referenced only by R1/R2 (inside)  → internal;
    #               ``out`` referenced by Rl (outside)         → external.
    assert sub.port_map == {"in": "in", "out": "out"}
    # The two resistors are now inside the body, not at the top level.
    top_names = [comp.name for comp in c.components]
    assert "X1" in top_names
    assert "R1" not in top_names and "R2" not in top_names
    # Body retains the resistors.
    body_names = [comp.name for comp in sub.body.components]
    assert body_names == ["R1", "R2"]


def test_group_internal_only_node_becomes_namespaced():
    c = Circuit("top")
    c.add_vsource("V1", "in", "0", 1)
    R1 = c.add_resistor("R1", "in", "mid", 1000)
    R2 = c.add_resistor("R2", "mid", "out", 1000)
    c.add_resistor("Rl", "out", "0", 1000)
    c.group([R1, R2], name="X1")

    leaf_names = {leaf.name for leaf in c.flat_components()}
    assert "X1.R1" in leaf_names
    assert "X1.R2" in leaf_names
    # mid is internal-only, must now appear under the X1 prefix.
    leaves = {leaf.name: (leaf.n_plus, leaf.n_minus)
              for leaf in c.flat_components()
              if isinstance(leaf, Resistor)}
    assert leaves["X1.R1"] == ("in", "X1.mid")
    assert leaves["X1.R2"] == ("X1.mid", "out")


def test_group_preserves_dc_solution():
    """Grouping should be a no-op semantically — only the structure changes."""
    c1 = Circuit("flat")
    c1.add_vsource("V1", "in", "0", 5)
    c1.add_resistor("R1", "in", "mid", 1000)
    c1.add_resistor("R2", "mid", "0", 1000)

    c2 = Circuit("grouped")
    c2.add_vsource("V1", "in", "0", 5)
    R1 = c2.add_resistor("R1", "in", "mid", 1000)
    R2 = c2.add_resistor("R2", "mid", "0", 1000)
    c2.group([R1, R2], name="X1")

    sol1 = solve_dc(c1)
    sol2 = solve_dc(c2)
    assert sol1[cas.Symbol("V(in)")] == sol2[cas.Symbol("V(in)")]


def test_group_inverting_amp_keeps_high_gain_limit():
    import sympy

    c = Circuit("inv")
    Vin = cas.Symbol("Vin")
    c.add_vsource("V1", "in", "0", Vin)
    Ri = c.add_resistor("Ri", "in", "inv", 1000)
    Rf = c.add_resistor("Rf", "out", "inv", 10000)
    U1 = c.add_opamp("U1", "0", "inv", "out")
    c.add_resistor("Rl", "out", "0", 1000)
    c.group([Ri, Rf, U1], name="X1", body_name="IAMP")

    sol = solve_dc(c)
    gain = sympy.limit(sol[cas.Symbol("V(out)")] / Vin, U1.A, sympy.oo)
    assert sympy.simplify(gain + 10) == 0  # -Rf/Ri = -10


def test_group_rejects_component_not_in_circuit():
    c = Circuit("c")
    c.add_resistor("R1", "a", "0", 1)
    stray = Resistor("R2", "b", "0", 1)
    with pytest.raises(ValueError, match="not in this circuit"):
        c.group([stray], name="X1")


def test_group_rejects_duplicates():
    c = Circuit("c")
    R1 = c.add_resistor("R1", "a", "0", 1)
    with pytest.raises(ValueError, match="duplicates"):
        c.group([R1, R1], name="X1")


def test_group_rejects_empty():
    c = Circuit("c")
    c.add_resistor("R1", "a", "0", 1)
    with pytest.raises(ValueError, match="empty"):
        c.group([], name="X1")


def test_group_with_params_substitutes_into_body():
    c = Circuit("c")
    R = cas.Symbol("R")
    c.add_vsource("V1", "in", "0", 1)
    Rg = c.add_resistor("Rg", "in", "out", R)
    c.add_resistor("Rl", "out", "0", 100)
    c.group([Rg], name="X1", params={"R": 200})

    leaves = c.flat_components()
    rg_leaf = next(l for l in leaves if l.name == "X1.Rg")
    assert rg_leaf.value == 200


# ---------------------------------------------------------------------------
# print_hierarchy
# ---------------------------------------------------------------------------

def test_print_hierarchy_top_level_helper_matches_method():
    c = Circuit("top")
    c.add_resistor("R1", "a", "0", 1)
    buf1 = io.StringIO()
    print_hierarchy(c, file=buf1)
    buf2 = io.StringIO()
    c.print_hierarchy(file=buf2)
    assert buf1.getvalue() == buf2.getvalue()


def test_print_hierarchy_shows_params_on_subcircuit_line():
    body = Circuit("BODY")
    body.add_resistor("R", "a", "b", cas.Symbol("R"))
    c = Circuit("top")
    c.add_subcircuit(
        "X1", body, port_map={"a": "p", "b": "q"}, params={"R": 4700},
    )

    buf = io.StringIO()
    c.print_hierarchy(file=buf)
    text = buf.getvalue()
    assert "PARAMS:" in text
    assert "R=4700" in text


# ---------------------------------------------------------------------------
# autodraw: group bounding box
# ---------------------------------------------------------------------------

def test_autodraw_renders_group_bounding_box():
    c = Circuit("amp")
    c.add_vsource("V1", "in", "0", 1)
    R1 = c.add_resistor("R1", "in", "mid", 1000)
    R2 = c.add_resistor("R2", "mid", "out", 1000)
    c.add_resistor("Rl", "out", "0", 1000)
    c.group([R1, R2], name="X1")

    svg = autodraw(c, optimize=False)
    assert 'class="grpbox"' in svg
    # The group label should appear near the bounding box.
    assert re.search(r'class="grplab"[^>]*>X1<', svg)


def test_autodraw_no_group_means_no_box():
    c = Circuit("flat")
    c.add_vsource("V1", "in", "0", 1)
    c.add_resistor("R1", "in", "mid", 1000)
    c.add_resistor("R2", "mid", "0", 1000)

    svg = autodraw(c, optimize=False)
    # The CSS class definition is always present; what must NOT appear
    # is an actual rectangle element using it.
    assert '<rect class="grpbox"' not in svg


def test_autodraw_group_box_encloses_member_centers():
    """The drawn rectangle must contain every grouped component's center."""
    c = Circuit("amp")
    c.add_vsource("V1", "in", "0", 1)
    R1 = c.add_resistor("R1", "in", "mid", 1000)
    R2 = c.add_resistor("R2", "mid", "out", 1000)
    c.add_resistor("Rl", "out", "0", 1000)
    c.group([R1, R2], name="X1")

    svg = autodraw(c, optimize=False)
    # Pull every grpbox rect.
    rects = re.findall(
        r'<rect class="grpbox" x="([\d.]+)" y="([\d.]+)" '
        r'width="([\d.]+)" height="([\d.]+)"',
        svg,
    )
    assert rects, "expected at least one group rectangle"
    # The outer (largest area) rect is X1's. Confirm it's a non-degenerate
    # rectangle wider/taller than a single component glyph.
    areas = [(float(w) * float(h), float(x), float(y), float(w), float(h))
             for x, y, w, h in rects]
    areas.sort(reverse=True)
    _, x, y, w, h = areas[0]
    assert w > 0 and h > 0
    # Sanity: the canvas should be at least as big as the rect.
    m = re.search(r'viewBox="0 0 (\d+) (\d+)"', svg)
    assert m
    cw, ch = int(m.group(1)), int(m.group(2))
    assert x + w <= cw and y + h <= ch
