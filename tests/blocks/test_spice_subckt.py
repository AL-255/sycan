"""SPICE parser tests for ``.SUBCKT`` / ``.ENDS`` user-defined subcircuits.

Covers the X-element dispatch (user subckt vs. built-in OPAMP / TRIODE),
forward references, nesting (subckts that instantiate other subckts),
shared bodies across multiple instances, and the standard error paths
(unknown subckt, pin-count mismatch, malformed ``.SUBCKT`` / ``.ENDS``,
circular references).
"""
import pytest

from sycan import (
    Circuit,
    OPAMP,
    Resistor,
    SubCircuit,
    cas,
    parse,
    solve_dc,
)


# ---------------------------------------------------------------------------
# Basic .SUBCKT instantiation
# ---------------------------------------------------------------------------

def test_subckt_simple_voltage_divider():
    netlist = """Voltage divider via .SUBCKT
.SUBCKT DIVIDER in out
R1 in mid 1k
R2 mid out 1k
.ENDS DIVIDER
V1 in 0 5
X1 in tap DIVIDER
W1 tap 0
.end
"""
    c = parse(netlist)
    sol = solve_dc(c)
    # 5 V across (R1 + R2) = 2 kΩ ⇒ 2.5 mA.
    assert sol[cas.Symbol("V(in)")] == 5
    assert sol[cas.Symbol("V(X1.mid)")] == cas.Rational(5, 2)
    assert sol[cas.Symbol("V(tap)")] == 0


def test_subckt_creates_subcircuit_instance():
    netlist = """trivial
.SUBCKT FOO a b
R1 a b 1k
.ENDS
V1 a 0 1
X1 a b FOO
.end
"""
    c = parse(netlist)
    insts = [x for x in c.components if isinstance(x, SubCircuit)]
    assert len(insts) == 1
    sc = insts[0]
    assert sc.name == "X1"
    assert sc.port_map == {"a": "a", "b": "b"}
    # Body has the resistor we declared.
    assert any(
        isinstance(c2, Resistor) and c2.name == "R1" for c2 in sc.body.components
    )


def test_subckt_forward_reference():
    """An ``X`` element referencing a subckt defined LATER must work."""
    netlist = """forward reference
V1 in 0 5
X1 in tap DIVIDER
W1 tap 0
.SUBCKT DIVIDER a b
R1 a c 1k
R2 c b 1k
.ENDS
.end
"""
    c = parse(netlist)
    leaves = c.flat_components()
    leaf_names = {leaf.name for leaf in leaves}
    assert "X1.R1" in leaf_names
    assert "X1.R2" in leaf_names


def test_subckt_case_insensitive_name():
    """``.SUBCKT FOO`` and ``X1 ... foo`` should match — SPICE is
    case-insensitive for subckt identifiers."""
    netlist = """case test
.SUBCKT MyDiv a b
R1 a b 1k
.ENDS
V1 a 0 1
X1 a b mydiv
.end
"""
    c = parse(netlist)
    flat_names = {leaf.name for leaf in c.flat_components()}
    assert "X1.R1" in flat_names


# ---------------------------------------------------------------------------
# Nesting
# ---------------------------------------------------------------------------

def test_subckt_nested_via_x_reference():
    """A subckt body that references another subckt — semantic nesting."""
    netlist = """nested .SUBCKT via X reference
.SUBCKT DIVIDER in out mid
R1 in mid 1k
R2 mid out 1k
.ENDS

.SUBCKT STAGE vin vout
X_d vin vout tap DIVIDER
R3 tap 0 1k
.ENDS

V1 src 0 9
X_top src dst STAGE
W_short dst 0
.end
"""
    c = parse(netlist)
    sol = solve_dc(c)
    # V_tap = 3 (the divider's mid-node, fed 9 V on one end and 0 on
    # both the other end and via R3 to ground).
    assert sol[cas.Symbol("V(X_top.tap)")] == 3
    assert sol[cas.Symbol("V(src)")] == 9
    assert sol[cas.Symbol("V(dst)")] == 0


def test_subckt_nested_three_levels_deep():
    netlist = """three-deep nesting
.SUBCKT INNER a b
R1 a b 1k
.ENDS

.SUBCKT MID p q
X_inner p q INNER
.ENDS

.SUBCKT OUTER x y
X_mid x y MID
.ENDS

V1 src 0 1
X_top src dst OUTER
W1 dst 0
.end
"""
    c = parse(netlist)
    flat = c.flat_components()
    leaf_names = {leaf.name for leaf in flat}
    # The innermost resistor lives at the bottom of the prefix chain.
    assert "X_top.X_mid.X_inner.R1" in leaf_names


def test_subckt_two_instances_share_body_independently():
    """Two X instances of the same subckt must namespace independently."""
    netlist = """two instances
.SUBCKT BUF in out
R in out 1k
.ENDS
V1 a 0 1
X1 a b BUF
X2 a c BUF
W1 b 0
W2 c 0
.end
"""
    c = parse(netlist)
    flat = c.flat_components()
    names = {leaf.name for leaf in flat}
    assert "X1.R" in names
    assert "X2.R" in names
    # Sanity: different SubCircuit instances exist at the top level.
    sc_names = {x.name for x in c.components if isinstance(x, SubCircuit)}
    assert sc_names == {"X1", "X2"}


# ---------------------------------------------------------------------------
# Mixing user subckts with built-ins
# ---------------------------------------------------------------------------

def test_subckt_body_can_use_builtin_opamp():
    """A user subckt body may instantiate the built-in OPAMP block."""
    netlist = """user subckt wraps OPAMP
.SUBCKT NONINV vin vout
Rg vinv 0 Rg
Rf vout vinv Rf
X_op vin vinv vout OPAMP A
.ENDS
V1 src 0 Vin
X1 src dst NONINV
.end
"""
    c = parse(netlist)
    sol = solve_dc(c)
    Vin, A, Rf, Rg = cas.symbols("Vin A Rf Rg")
    out = sol[cas.Symbol("V(dst)")]
    # High-gain limit: closed-loop gain 1 + Rf/Rg.
    assert cas.simplify(cas.limit(out, A, cas.oo) - Vin * (1 + Rf / Rg)) == 0


def test_user_subckt_takes_priority_over_builtin():
    """A user-defined ``OPAMP`` subckt should shadow the built-in
    block when the X-element's last token names the user one."""
    netlist = """shadowing OPAMP
.SUBCKT OPAMP a b c
R_short a b 1
R_load b c 1
.ENDS
V1 a 0 1
X1 a b c OPAMP
W1 c 0
.end
"""
    c = parse(netlist)
    insts = [x for x in c.components if isinstance(x, SubCircuit)]
    assert len(insts) == 1
    # The body should have the user's two resistors, NOT a VCVS.
    body_kinds = {type(comp).__name__ for comp in insts[0].body.components}
    assert body_kinds == {"Resistor"}


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------

def test_subckt_pin_count_mismatch_raises():
    netlist = """mismatch
.SUBCKT FOO a b
R1 a b 1k
.ENDS
X1 only_one FOO
.end
"""
    with pytest.raises(ValueError, match="pin node"):
        parse(netlist)


def test_subckt_unknown_name_raises():
    netlist = """unknown
V1 a 0 1
X1 a b c BLOOP
.end
"""
    with pytest.raises(ValueError, match="unknown subcircuit"):
        parse(netlist)


def test_subckt_end_inside_block_raises():
    """``.end`` while still inside a ``.SUBCKT`` block is rejected."""
    netlist = """end inside subckt
.SUBCKT FOO a b
R1 a b 1k
.end
"""
    with pytest.raises(ValueError, match=r"\.end inside \.SUBCKT"):
        parse(netlist)


def test_subckt_eof_without_ends_raises():
    """A ``.SUBCKT`` block with no closing ``.ENDS`` and no ``.end`` is rejected."""
    netlist = """missing ends, no .end either
.SUBCKT FOO a b
R1 a b 1k
"""
    with pytest.raises(ValueError, match="missing .ENDS"):
        parse(netlist)


def test_subckt_nested_in_source_raises():
    netlist = """nested in source
.SUBCKT OUTER a b
.SUBCKT INNER c d
R c d 1k
.ENDS
.ENDS
.end
"""
    with pytest.raises(ValueError, match="nested .SUBCKT"):
        parse(netlist)


def test_subckt_duplicate_name_raises():
    netlist = """duplicate
.SUBCKT FOO a b
R1 a b 1k
.ENDS
.SUBCKT FOO a b
R2 a b 2k
.ENDS
.end
"""
    with pytest.raises(ValueError, match="duplicate"):
        parse(netlist)


def test_subckt_ends_name_mismatch_raises():
    netlist = """ends mismatch
.SUBCKT FOO a b
R1 a b 1k
.ENDS BAR
.end
"""
    with pytest.raises(ValueError, match="does not match"):
        parse(netlist)


def test_subckt_circular_reference_raises():
    """A → B → A is detected and reported rather than infinite-looping."""
    netlist = """circular
.SUBCKT A p q
X_b p q B
.ENDS
.SUBCKT B p q
X_a p q A
.ENDS
V1 a 0 1
X1 a b A
.end
"""
    with pytest.raises(ValueError, match="circular"):
        parse(netlist)


# ---------------------------------------------------------------------------
# Backward compatibility — existing built-in dispatch still works
# ---------------------------------------------------------------------------

def test_x_opamp_builtin_still_works_unchanged():
    netlist = """built-in OPAMP, no user subckts present
V1 in 0 Vin
Ri in inv Ri
Rf out inv Rf
X1 0 inv out OPAMP A
.end
"""
    c = parse(netlist)
    sol = solve_dc(c)
    Vin, A, Ri, Rf = cas.symbols("Vin A Ri Rf")
    gain = sol[cas.Symbol("V(out)")] / Vin
    assert cas.simplify(cas.limit(gain, A, cas.oo) + Rf / Ri) == 0


def test_x_triode_builtin_still_works_unchanged():
    netlist = """built-in TRIODE
Vg grid 0 V_g
Vp plate 0 V_p
X1 plate grid 0 TRIODE K mu
.end
"""
    c = parse(netlist)
    # Just exercise parsing — solving the nonlinear is covered elsewhere.
    leaf_names = {leaf.name for leaf in c.flat_components()}
    assert "X1" in leaf_names  # Triode is a leaf, not a SubCircuit
