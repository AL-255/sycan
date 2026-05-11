"""Hierarchical-design parameter propagation and SPICE I/O round-trip.

Covers the new ``SubCircuit.params`` mechanism and the matching
SPICE writer / parser support for ``.SUBCKT ... PARAMS:`` and
``Xinst ... PARAMS:`` syntax.
"""
import pytest

from sycan import (
    Circuit,
    Resistor,
    SubCircuit,
    cas,
    parse,
    solve_dc,
    to_spice,
)


# ---------------------------------------------------------------------------
# Parameter substitution at expand time
# ---------------------------------------------------------------------------

def test_params_substitute_symbolic_placeholders_on_leaves():
    body = Circuit("amp")
    R = cas.Symbol("R")
    body.add_resistor("R1", "in", "out", R)

    parent = Circuit("top")
    parent.add_subcircuit(
        "X1", body, port_map={"in": "in", "out": "out"},
        params={"R": 1000},
    )

    leaves = parent.flat_components()
    assert len(leaves) == 1
    leaf = leaves[0]
    assert isinstance(leaf, Resistor)
    assert leaf.value == 1000


def test_params_propagate_into_nested_subcircuits():
    """Outer-scope params reach unmodified inner-leaf placeholders."""
    R = cas.Symbol("R")
    inner = Circuit("inner")
    inner.add_resistor("R1", "a", "b", R)

    outer = Circuit("outer")
    outer.add_subcircuit("Y1", inner, port_map={"a": "p", "b": "q"})

    top = Circuit("top")
    top.add_subcircuit(
        "X1", outer, port_map={"p": "P", "q": "Q"}, params={"R": 47},
    )

    leaves = top.flat_components()
    assert len(leaves) == 1
    assert leaves[0].name == "X1.Y1.R1"
    assert leaves[0].value == 47


def test_inner_params_override_outer_for_same_key():
    R = cas.Symbol("R")
    inner = Circuit("inner")
    inner.add_resistor("R1", "a", "b", R)

    outer = Circuit("outer")
    outer.add_subcircuit(
        "Y1", inner, port_map={"a": "p", "b": "q"}, params={"R": 999},
    )

    top = Circuit("top")
    top.add_subcircuit(
        "X1", outer, port_map={"p": "P", "q": "Q"}, params={"R": 100},
    )

    leaves = top.flat_components()
    assert leaves[0].value == 999  # inner override wins


def test_params_keys_not_in_body_are_silently_ignored():
    """Unused params don't crash — they just don't substitute anything."""
    body = Circuit("body")
    body.add_resistor("R1", "a", "b", 1000)

    top = Circuit("top")
    top.add_subcircuit(
        "X1", body, port_map={"a": "x", "b": "0"},
        params={"unused_key": 42},
    )
    leaves = top.flat_components()
    assert leaves[0].value == 1000


def test_params_solve_dc_matches_inlined_resistance():
    R = cas.Symbol("R_div")
    body = Circuit("DIV")
    body.add_resistor("Ra", "in", "mid", R)
    body.add_resistor("Rb", "mid", "out", R)

    c = Circuit("top")
    c.add_vsource("V1", "in", "0", 5)
    c.add_subcircuit(
        "X1", body, port_map={"in": "in", "out": "tap"},
        params={"R_div": 1000},
    )
    c.add_subcircuit(
        "X2", body, port_map={"in": "tap", "out": "out"},
        params={"R_div": 2000},
    )
    c.add_resistor("Rl", "out", "0", 1000)

    sol = solve_dc(c)
    # Two divider stages share the body but use different R values.
    # Total resistance: 2k from X1, 4k from X2, 1k load.
    # V(out) = 5 * 1k / (2k + 4k + 1k) = 5/7
    assert sol[cas.Symbol("V(out)")] == cas.Rational(5, 7)


# ---------------------------------------------------------------------------
# SPICE parser: PARAMS on .SUBCKT and X
# ---------------------------------------------------------------------------

def test_spice_parser_subckt_with_default_params():
    netlist = """divider with default
.SUBCKT DIV in out PARAMS: R=1k
R1 in mid R
R2 mid out R
.ENDS DIV
V1 in 0 5
X1 in tap DIV
W1 tap 0
.end
"""
    c = parse(netlist)
    sol = solve_dc(c)
    # Default R=1k, two-stage to ground => V(mid) = 2.5
    assert sol[cas.Symbol("V(X1.mid)")] == cas.Rational(5, 2)


def test_spice_parser_x_params_overrides_subckt_default():
    netlist = """divider with override
.SUBCKT DIV in out PARAMS: R=1k
R1 in mid R
R2 mid out R
.ENDS DIV
V1 in 0 5
X1 in tap DIV PARAMS: R=2k
W1 tap 0
.end
"""
    c = parse(netlist)
    leaves = {leaf.name: leaf.value for leaf in c.flat_components()
              if isinstance(leaf, Resistor)}
    assert leaves["X1.R1"] == 2000
    assert leaves["X1.R2"] == 2000


def test_spice_parser_x_params_only_at_instance():
    """Subckt with no defaults; instance supplies all params."""
    netlist = """instance-supplied params
.SUBCKT DIV in out
R1 in out R
.ENDS
V1 in 0 1
X1 in 0 DIV PARAMS: R=4700
.end
"""
    c = parse(netlist)
    leaves = {leaf.name: leaf.value for leaf in c.flat_components()
              if isinstance(leaf, Resistor)}
    assert leaves["X1.R1"] == 4700


def test_spice_parser_params_with_spaces_around_equals():
    """``R = 1k`` (split across whitespace) parses the same as ``R=1k``."""
    netlist = """spaced params
.SUBCKT DIV in out PARAMS: R = 1k
R1 in out R
.ENDS
V1 in 0 1
X1 in 0 DIV PARAMS: R = 2k
.end
"""
    c = parse(netlist)
    leaves = {leaf.name: leaf.value for leaf in c.flat_components()
              if isinstance(leaf, Resistor)}
    assert leaves["X1.R1"] == 2000


def test_spice_parser_params_rejected_on_builtin_opamp():
    netlist = """params on built-in OPAMP must be rejected
V1 in 0 1
X1 in inv out OPAMP PARAMS: A=1e6
.end
"""
    with pytest.raises(ValueError, match="PARAMS:"):
        parse(netlist)


# ---------------------------------------------------------------------------
# to_spice writer
# ---------------------------------------------------------------------------

def test_to_spice_emits_basic_resistor_divider():
    c = Circuit("flat")
    c.add_vsource("V1", "in", "0", 5)
    c.add_resistor("R1", "in", "mid", 1000)
    c.add_resistor("R2", "mid", "0", 1000)
    text = to_spice(c)
    assert "V1 in 0 5" in text
    assert "R1 in mid 1000" in text
    assert "R2 mid 0 1000" in text
    assert text.strip().endswith(".end")


def test_to_spice_subckt_block_and_params():
    body = Circuit("DIV")
    R = cas.Symbol("R")
    body.add_resistor("Ra", "in", "mid", R)
    body.add_resistor("Rb", "mid", "out", R)

    c = Circuit("top")
    c.add_vsource("V1", "in", "0", 5)
    c.add_subcircuit(
        "X1", body, port_map={"in": "in", "out": "tap"},
        params={"R": 1000},
    )

    text = to_spice(c)
    assert ".SUBCKT DIV in out" in text
    assert "Ra in mid R" in text
    assert "Rb mid out R" in text
    assert ".ENDS DIV" in text
    assert "X1 in tap DIV PARAMS: R=1000" in text


def test_to_spice_round_trip_preserves_dc_solution():
    body = Circuit("DIV")
    R = cas.Symbol("R")
    body.add_resistor("Ra", "in", "mid", R)
    body.add_resistor("Rb", "mid", "out", R)

    orig = Circuit("top")
    orig.add_vsource("V1", "in", "0", 5)
    orig.add_subcircuit(
        "X1", body, port_map={"in": "in", "out": "tap"},
        params={"R": 1000},
    )
    orig.add_subcircuit(
        "X2", body, port_map={"in": "tap", "out": "out"},
        params={"R": 2000},
    )
    orig.add_resistor("Rl", "out", "0", 1000)

    text = to_spice(orig)
    rt = parse(text)
    assert solve_dc(rt)[cas.Symbol("V(tap)")] == \
           solve_dc(orig)[cas.Symbol("V(tap)")]


def test_to_spice_shared_body_emits_one_subckt_block():
    """Two X-instances with the same body should share one .SUBCKT def."""
    body = Circuit("BUF")
    body.add_resistor("R", "in", "out", cas.Symbol("R"))

    c = Circuit("top")
    c.add_subcircuit("X1", body, port_map={"in": "a", "out": "b"},
                     params={"R": 100})
    c.add_subcircuit("X2", body, port_map={"in": "b", "out": "c"},
                     params={"R": 200})

    text = to_spice(c)
    assert text.count(".SUBCKT BUF") == 1
    assert "X1 a b BUF PARAMS: R=100" in text
    assert "X2 b c BUF PARAMS: R=200" in text


def test_to_spice_opamp_emits_builtin_x_form():
    """Built-in OPAMP wrappers round-trip via ``Xname ... OPAMP A``."""
    c = Circuit("inv")
    A = cas.Symbol("A")
    c.add_vsource("V1", "in", "0", 1)
    c.add_resistor("Ri", "in", "inv", 1000)
    c.add_resistor("Rf", "out", "inv", 10000)
    c.add_opamp("X1", "0", "inv", "out", A)

    text = to_spice(c)
    assert "X1 0 inv out OPAMP A" in text
    # Ensure no .SUBCKT block was generated for the built-in OPAMP.
    assert ".SUBCKT" not in text
