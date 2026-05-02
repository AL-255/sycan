"""Hierarchical SubCircuit / OPAMP feature tests.

Verifies that:

* :class:`SubCircuit` wraps an inner circuit, exposes pins, and
  flattens correctly into renamed/rerouted leaf components.
* Nested subcircuits expand recursively with composed node remaps.
* Internal-only nodes are namespaced (``<instance>.<inner>``); ground
  is shared with the parent.
* :class:`OPAMP` wired into a feedback network reproduces the standard
  inverting / non-inverting closed-loop gains in the limit ``A → ∞``.
* :meth:`Circuit.print_hierarchy` reports the full subcircuit summary
  and tree.
* The SPICE parser accepts ``Xxxx ... OPAMP [A]``.
"""
import io

from sycan import (
    Circuit,
    OPAMP,
    Resistor,
    SubCircuit,
    VCVS,
    VoltageSource,
    cas,
    parse,
    solve_ac,
    solve_dc,
)


# ---------------------------------------------------------------------------
# Flattening
# ---------------------------------------------------------------------------

def test_subcircuit_flattens_to_leaves_with_namespaced_names():
    body = Circuit("OPAMP")
    body.add_vcvs("E1", "out", "0", "in_p", "in_n", cas.Symbol("A"))

    parent = Circuit("top")
    parent.add_subcircuit(
        "X1", body,
        port_map={"in_p": "vp", "in_n": "vn", "out": "vo"},
    )

    flat = parent.flat_components()
    assert len(flat) == 1
    leaf = flat[0]
    assert isinstance(leaf, VCVS)
    assert leaf.name == "X1.E1"
    # Pins resolve to parent-scope nodes.
    assert leaf.n_plus == "vo"
    assert leaf.n_minus == "0"
    assert leaf.nc_plus == "vp"
    assert leaf.nc_minus == "vn"


def test_subcircuit_internal_nodes_are_namespaced():
    """A node that exists in the body but isn't a pin gets prefixed."""
    body = Circuit("two-stage")
    body.add_resistor("R1", "in", "mid", cas.Symbol("R1"))
    body.add_resistor("R2", "mid", "out", cas.Symbol("R2"))

    parent = Circuit("top")
    parent.add_subcircuit(
        "X1", body,
        port_map={"in": "vin", "out": "vout"},
    )

    leaf_nodes = {(c.name, c.n_plus, c.n_minus) for c in parent.flat_components()}
    assert ("X1.R1", "vin", "X1.mid") in leaf_nodes
    assert ("X1.R2", "X1.mid", "vout") in leaf_nodes
    # Parent's node registry should know about the namespaced internal node.
    assert "X1.mid" in parent.nodes


def test_subcircuit_ground_is_shared_with_parent():
    body = Circuit("body")
    body.add_resistor("R1", "p", "0", cas.Symbol("R"))

    parent = Circuit("top")
    parent.add_subcircuit("X1", body, port_map={"p": "node_a"})

    flat = parent.flat_components()
    assert len(flat) == 1
    assert flat[0].n_minus == "0"  # Not "X1.0" — ground is universal.


def test_nested_subcircuits_compose_remaps():
    """A subcircuit inside a subcircuit should resolve nodes through
    both layers of pin mapping."""
    inner_body = Circuit("inner")
    inner_body.add_resistor("R", "a", "b", cas.Symbol("R"))

    outer_body = Circuit("outer")
    outer_body.add_subcircuit(
        "Y1", inner_body,
        port_map={"a": "p", "b": "q"},
    )

    parent = Circuit("top")
    parent.add_subcircuit(
        "X1", outer_body,
        port_map={"p": "TOP_P", "q": "TOP_Q"},
    )

    flat = parent.flat_components()
    assert len(flat) == 1
    leaf = flat[0]
    assert leaf.name == "X1.Y1.R"
    assert leaf.n_plus == "TOP_P"
    assert leaf.n_minus == "TOP_Q"


def test_nested_subcircuit_internal_node_double_namespaced():
    """An internal node inside a nested subcircuit gets the full prefix."""
    inner_body = Circuit("inner")
    inner_body.add_resistor("R1", "a", "mid", cas.Symbol("R1"))
    inner_body.add_resistor("R2", "mid", "b", cas.Symbol("R2"))

    outer_body = Circuit("outer")
    outer_body.add_subcircuit("Y1", inner_body, port_map={"a": "p", "b": "q"})

    parent = Circuit("top")
    parent.add_subcircuit("X1", outer_body, port_map={"p": "TP", "q": "TQ"})

    flat_names = {c.name for c in parent.flat_components()}
    assert flat_names == {"X1.Y1.R1", "X1.Y1.R2"}
    # mid is internal to the inner body — namespaced under both prefixes.
    leaf_nodes = {c.name: (c.n_plus, c.n_minus) for c in parent.flat_components()}
    assert leaf_nodes["X1.Y1.R1"] == ("TP", "X1.Y1.mid")
    assert leaf_nodes["X1.Y1.R2"] == ("X1.Y1.mid", "TQ")


def test_subcircuit_validation_rejects_unknown_pin():
    body = Circuit("body")
    body.add_resistor("R1", "p", "0", cas.Symbol("R"))

    try:
        SubCircuit("X1", body, port_map={"unknown": "x"})
    except ValueError as e:
        assert "unknown" in str(e)
    else:
        raise AssertionError("expected ValueError for unknown pin")


# ---------------------------------------------------------------------------
# OPAMP block
# ---------------------------------------------------------------------------

def test_opamp_subcircuit_inverting_limit():
    """OPAMP wrapped in feedback recovers -Rf/Ri as A -> oo."""
    c = Circuit("inv")
    Vin, A, Ri, Rf = cas.symbols("Vin A Ri Rf")
    c.add_vsource("V1", "in", "0", Vin)
    c.add_resistor("Ri", "in", "inv", Ri)
    c.add_resistor("Rf", "out", "inv", Rf)
    c.add_opamp("X1", "0", "inv", "out", A)

    sol = solve_dc(c)
    gain = sol[cas.Symbol("V(out)")] / Vin
    assert cas.simplify(cas.limit(gain, A, cas.oo) + Rf / Ri) == 0


def test_opamp_subcircuit_noninverting_limit():
    """1 + Rf/Rg in the high-gain limit, virtual short on the - input."""
    c = Circuit("noninv")
    Vin, A, Rf, Rg = cas.symbols("Vin A Rf Rg")
    c.add_vsource("V1", "in", "0", Vin)
    c.add_resistor("Rf", "out", "inv", Rf)
    c.add_resistor("Rg", "inv", "0", Rg)
    c.add_opamp("X1", "in", "inv", "out", A)

    sol = solve_dc(c)
    gain = sol[cas.Symbol("V(out)")] / Vin
    assert cas.simplify(cas.limit(gain, A, cas.oo) - (1 + Rf / Rg)) == 0
    # Virtual short: V(inv) -> V(in) as A -> oo.
    assert (
        cas.simplify(cas.limit(sol[cas.Symbol("V(inv)")], A, cas.oo) - Vin) == 0
    )


def test_opamp_default_gain_symbol_is_per_instance():
    op1 = OPAMP("X1", "p", "n", "o")
    op2 = OPAMP("X2", "p", "n", "o")
    assert op1.A != op2.A
    assert str(op1.A) == "A_X1"
    assert str(op2.A) == "A_X2"


def test_two_opamps_in_one_circuit_have_independent_gains():
    """Two OPAMP instances should not collide when stamped together."""
    c = Circuit("dual-opamp")
    Vin = cas.Symbol("Vin")
    c.add_vsource("V1", "in", "0", Vin)
    # First stage: unity buffer.
    c.add_opamp("X1", "in", "mid", "mid")
    # Second stage: another unity buffer.
    c.add_opamp("X2", "mid", "out", "out")

    sol = solve_dc(c)
    A1 = cas.Symbol("A_X1")
    A2 = cas.Symbol("A_X2")
    out = sol[cas.Symbol("V(out)")]
    # Take both gains to infinity — output should track input.
    out = cas.limit(out, A1, cas.oo)
    out = cas.limit(out, A2, cas.oo)
    assert cas.simplify(out - Vin) == 0


# ---------------------------------------------------------------------------
# Hierarchy printing
# ---------------------------------------------------------------------------

def test_print_hierarchy_lists_subcircuits_and_tree():
    inner = Circuit("INNER")
    inner.add_opamp("U1", "ip", "in_", "ox")

    c = Circuit("demo")
    c.add_resistor("Rload", "out", "0", 1)
    c.add_subcircuit("X1", inner, port_map={"ip": "a", "in_": "b", "ox": "out"})
    c.add_opamp("X2", "a", "b", "alt")

    buf = io.StringIO()
    c.print_hierarchy(file=buf)
    text = buf.getvalue()

    # Header.
    assert "Circuit 'demo'" in text
    # Summary by class. X1 itself is a generic SubCircuit, X1.U1 is OPAMP.
    assert "OPAMP  x2" in text
    assert "SubCircuit  x1" in text
    assert "X1.U1" in text  # nested subcircuit shown with dotted path
    assert "X2" in text
    # Tree includes leaves and the recursive expansion.
    assert "Rload" in text
    assert "X1 [SubCircuit]" in text
    assert "U1 [OPAMP]" in text
    assert "E1 [VCVS]" in text


def test_print_hierarchy_no_subcircuits():
    c = Circuit("flat")
    c.add_resistor("R1", "in", "out", 1)
    buf = io.StringIO()
    c.print_hierarchy(file=buf)
    text = buf.getvalue()
    assert "Subcircuits: (none)" in text
    assert "R1 [Resistor]" in text


# ---------------------------------------------------------------------------
# SPICE parser
# ---------------------------------------------------------------------------

def test_spice_parser_x_opamp_inverting():
    netlist = """inverting via X..OPAMP
V1 in 0 Vin
Ri in inv Ri
Rf out inv Rf
X1 0 inv out OPAMP A
.end
"""
    c = parse(netlist)
    Vin, A, Ri, Rf = cas.symbols("Vin A Ri Rf")
    sol = solve_dc(c)
    gain = sol[cas.Symbol("V(out)")] / Vin
    assert cas.simplify(cas.limit(gain, A, cas.oo) + Rf / Ri) == 0
    # Default-gain form (A omitted) should also parse.
    netlist_default = """inverting via X..OPAMP, default gain
V1 in 0 Vin
Ri in inv Ri
Rf out inv Rf
X1 0 inv out OPAMP
.end
"""
    c2 = parse(netlist_default)
    A2 = cas.Symbol("A_X1")
    sol2 = solve_dc(c2)
    g2 = sol2[cas.Symbol("V(out)")] / Vin
    assert cas.simplify(cas.limit(g2, A2, cas.oo) + Rf / Ri) == 0


# ---------------------------------------------------------------------------
# AC analysis crosses the subcircuit boundary
# ---------------------------------------------------------------------------

def test_subcircuit_ac_buffer_gain_one_in_high_gain_limit():
    c = Circuit("buffer")
    s = cas.Symbol("s")
    Vin = cas.Symbol("Vin")
    A = cas.Symbol("A")
    c.add_vsource("V1", "in", "0", value=0, ac_value=Vin)
    # Unity buffer: + input from Vin, - input tied to output, output drives 'out'.
    c.add_opamp("X1", "in", "out", "out", A)
    sol = solve_ac(c, s=s)
    out = sol[cas.Symbol("V(out)")]
    assert cas.simplify(cas.limit(out, A, cas.oo) - Vin) == 0
