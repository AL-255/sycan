"""Visual + structural tests for :func:`sycan.autodraw`.

Each test renders an SVG into ``tests/drawing/diagrams/`` so the output
can be eyeballed in a browser. The asserts below only check structure
(every component box is emitted, every spine port is labelled,
nothing is empty), and that wires never cross a component bounding
box (the central layout invariant).

Run ``pytest tests/drawing -q`` to regenerate the SVGs.
"""
from __future__ import annotations

import re
from pathlib import Path

from sycan import autodraw, parse
from sycan.autodraw import BOX_H, BOX_W


DIAGRAM_DIR = Path(__file__).parent / "diagrams"
DIAGRAM_DIR.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Helpers — extract structural facts from the emitted SVG without parsing
# heavyweight XML; the format is line-oriented and predictable.
# ---------------------------------------------------------------------------
_RECT_RE = re.compile(
    r'<rect class="comp" data-comp="([^"]+)" data-name="([^"]+)" '
    r'x="([-\d.]+)" y="([-\d.]+)" width="([-\d.]+)" height="([-\d.]+)"'
)
_GLYPH_G_RE = re.compile(
    r'<g data-comp="([^"]+)" data-name="([^"]+)" '
    r'data-x="([-\d.]+)" data-y="([-\d.]+)" '
    r'data-bbox-w="([-\d.]+)" data-bbox-h="([-\d.]+)"'
)
_POLY_RE = re.compile(
    r'<polyline class="(wire|net|rail)" data-net="([^"]+)" points="([^"]+)"'
)


def _components(svg: str) -> list[tuple[str, str, float, float, float, float]]:
    """Return [(kind, name, x, y, w, h), ...] for every component box.

    Matches both the default ``<rect class="comp" ...>`` placeholder
    form and the per-instance ``<g data-comp=... data-x=... data-y=...
    data-bbox-w=... data-bbox-h=...>`` wrappers emitted when a glyph
    is present.
    """
    out = []
    for m in _RECT_RE.finditer(svg):
        kind, name, x, y, w, h = m.groups()
        out.append((kind, name, float(x), float(y), float(w), float(h)))
    for m in _GLYPH_G_RE.finditer(svg):
        kind, name, x, y, w, h = m.groups()
        out.append((kind, name, float(x), float(y), float(w), float(h)))
    return out


def _wires(svg: str) -> list[tuple[str, list[tuple[float, float]]]]:
    """Return [(net, [(x, y), ...]), ...] for every polyline."""
    out = []
    for m in _POLY_RE.finditer(svg):
        cls, net, pts_raw = m.groups()
        pts = []
        for tok in pts_raw.strip().split():
            xs, ys = tok.split(",")
            pts.append((float(xs), float(ys)))
        out.append((net, pts))
    return out


def _segments(pts):
    return list(zip(pts[:-1], pts[1:]))


def _seg_intersects_rect(seg, rect):
    """Does an axis-aligned segment cross *into* a rectangle?

    Endpoints touching the rectangle's perimeter are allowed (those are
    legitimate pin-tip connections). The segment is rejected only if it
    has any sub-segment strictly inside the rectangle.
    """
    (x1, y1), (x2, y2) = seg
    rx, ry, rw, rh = rect
    eps = 0.5
    rx0, ry0, rx1, ry1 = rx + eps, ry + eps, rx + rw - eps, ry + rh - eps

    if x1 == x2:  # vertical
        if not (rx0 < x1 < rx1):
            return False
        ymin, ymax = min(y1, y2), max(y1, y2)
        return ymin < ry1 and ymax > ry0
    if y1 == y2:  # horizontal
        if not (ry0 < y1 < ry1):
            return False
        xmin, xmax = min(x1, x2), max(x1, x2)
        return xmin < rx1 and xmax > rx0
    # Diagonal; we don't generate them, so flag if found.
    return False


def _pads(svg: str) -> list[tuple[float, float]]:
    """All pin-pad ``<circle class="pinpad">`` centres in the SVG."""
    return [
        (float(x), float(y))
        for x, y in re.findall(
            r'<circle class="pinpad" cx="([-\d.]+)" cy="([-\d.]+)" r="2" />',
            svg,
        )
    ]


def _column_x_of(comp, pads):
    """Return the x-coordinate that anchors a component to its column.

    With mixed-width glyphs, two components that share a column can
    have offset bbox centres — what physically aligns is the *spine*
    pin (the topmost pad attached to the component). This helper
    finds the pads that fall inside (or on the edge of) the component
    bbox and picks the topmost one's x; tests use it as a robust
    "which column is this in" key.
    """
    _kind, _name, x, y, w, h = comp
    box_pads = [
        (px, py) for px, py in pads
        if x - 0.5 <= px <= x + w + 0.5
        and y - 0.5 <= py <= y + h + 0.5
    ]
    if not box_pads:
        return x + w / 2.0
    return min(box_pads, key=lambda p: p[1])[0]


def _no_wire_crosses_component(svg: str) -> tuple[bool, str]:
    """Routing invariant: wires must never *pass through* a component
    box. A segment is allowed to enter (or touch) a box only when one
    of its endpoints is a pin pad belonging to that box — pin pads
    inside the bbox are the legitimate destination for a wire ending
    on that component (some glyphs draw their port markers a few
    pixels in from the bbox edge).
    """
    comps = _components(svg)
    pads = _pads(svg)
    wires = _wires(svg)
    for net, pts in wires:
        for seg in _segments(pts):
            for kind, name, x, y, w, h in comps:
                if not _seg_intersects_rect(seg, (x, y, w, h)):
                    continue
                # Pads that physically lie inside this component's bbox
                # (with a 0.5-px tolerance) — those are the box's own
                # pin pads.
                box_pads = [
                    (px, py) for px, py in pads
                    if x - 0.5 <= px <= x + w + 0.5
                    and y - 0.5 <= py <= y + h + 0.5
                ]
                seg_endpoint_on_pad = any(
                    abs(ex - bx) < 0.6 and abs(ey - by) < 0.6
                    for (ex, ey) in (seg[0], seg[1])
                    for bx, by in box_pads
                )
                if seg_endpoint_on_pad:
                    continue
                return False, (
                    f"net {net} segment {seg} crosses {kind} {name} "
                    f"@ ({x},{y},{w},{h})"
                )
    return True, ""


def _save(name: str, svg: str) -> None:
    (DIAGRAM_DIR / f"{name}.svg").write_text(svg)


def _common_assertions(svg: str, expected_box_names: set[str]) -> None:
    assert svg.startswith("<svg")
    assert svg.rstrip().endswith("</svg>")
    comps = _components(svg)
    got = {name for _kind, name, *_ in comps}
    missing = expected_box_names - got
    assert not missing, f"missing components: {missing}; got {got}"
    ok, why = _no_wire_crosses_component(svg)
    assert ok, why


# ---------------------------------------------------------------------------
# Test 1 — Resistive voltage divider. Two resistors stacked between
# VDD and GND. Expect: one branch column, both Rs in the same column.
# ---------------------------------------------------------------------------
NETLIST_DIVIDER = """\
voltage divider
V1 VDD 0 5
R1 VDD mid 1k
R2 mid 0 1k
.end
"""


def test_voltage_divider():
    svg = autodraw(NETLIST_DIVIDER, power_nets=("VDD",))
    _save("01_voltage_divider", svg)
    _common_assertions(svg, {"V1", "R1", "R2"})

    # R1 above R2 in the same column (within COL_W/2 in x).
    pads = _pads(svg); comps = {name: (_column_x_of(c, pads), y + h / 2) for c in _components(svg) for _kind, name, x, y, w, h in [c]}
    r1, r2 = comps["R1"], comps["R2"]
    assert abs(r1[0] - r2[0]) < 5, "R1 and R2 should share a column"
    assert r1[1] < r2[1], "R1 should be above R2"


# ---------------------------------------------------------------------------
# Test 2 — Common-source NMOS amp: R_L between VDD and drain, M1 from
# drain to GND with input at the gate. Expect a single column with
# R_L on top of M1 and the gate sticking out to the side.
# ---------------------------------------------------------------------------
NETLIST_CS_AMP = """\
CS amplifier
Vdd VDD 0 1.8
Vin gate 0 0.7
RL VDD drain 10k
M1 drain gate 0 NMOS_L1 mu_n Cox W L V_TH
.end
"""


def test_common_source_amp():
    svg = autodraw(NETLIST_CS_AMP)
    _save("02_common_source", svg)
    _common_assertions(svg, {"Vdd", "Vin", "RL", "M1"})

    pads = _pads(svg); comps = {name: (_column_x_of(c, pads), y + h / 2) for c in _components(svg) for _kind, name, x, y, w, h in [c]}
    rl, m1 = comps["RL"], comps["M1"]
    assert abs(rl[0] - m1[0]) < 5, "RL and M1 should share a column"
    assert rl[1] < m1[1], "RL on top of M1"


# ---------------------------------------------------------------------------
# Test 3 — NMOS current mirror. Two NMOS sharing a gate, sources to GND,
# drains separately. Expect two adjacent columns; gate net forces a
# horizontal route between them.
# ---------------------------------------------------------------------------
NETLIST_MIRROR = """\
nmos current mirror
Vdd VDD 0 1.8
RREF VDD dref 10k
M1 dref dref 0 NMOS_L1 mu_n Cox W L V_TH
RL  VDD dout 5k
M2 dout dref 0 NMOS_L1 mu_n Cox W L V_TH
.end
"""


def test_current_mirror():
    svg = autodraw(NETLIST_MIRROR)
    _save("03_current_mirror", svg)
    _common_assertions(svg, {"Vdd", "RREF", "M1", "RL", "M2"})

    # Two distinct columns for the two stacks.
    pads = _pads(svg); comps = {name: (_column_x_of(c, pads), y + h / 2) for c in _components(svg) for _kind, name, x, y, w, h in [c]}
    assert abs(comps["M1"][0] - comps["RREF"][0]) < 5
    assert abs(comps["M2"][0] - comps["RL"][0]) < 5
    assert comps["M1"][0] != comps["M2"][0], "M1 and M2 must be in different columns"


# ---------------------------------------------------------------------------
# Test 4 — NPN common-emitter amp with an emitter degeneration resistor.
# Expect a 3-tall stack: RC, Q1, RE; base wire pulls in from the side.
# ---------------------------------------------------------------------------
NETLIST_CE_BJT = """\
NPN common emitter
Vdd VDD 0 5
Vbb base 0 0.7
RC VDD col 4.7k
Q1 col base emi NPN 1e-15 100 1
RE emi 0 1k
.end
"""


def test_ce_bjt_with_degen():
    svg = autodraw(NETLIST_CE_BJT)
    _save("04_ce_bjt", svg)
    _common_assertions(svg, {"Vdd", "Vbb", "RC", "Q1", "RE"})

    pads = _pads(svg); comps = {name: (_column_x_of(c, pads), y + h / 2) for c in _components(svg) for _kind, name, x, y, w, h in [c]}
    rc, q1, re_ = comps["RC"], comps["Q1"], comps["RE"]
    assert abs(rc[0] - q1[0]) < 5 and abs(q1[0] - re_[0]) < 5, \
        "RC, Q1, RE should stack into one column"
    assert rc[1] < q1[1] < re_[1]


# ---------------------------------------------------------------------------
# Test 5 — Differential pair (NMOS) with tail current source. Two NMOS
# whose sources merge on a tail node, which sinks through I_TAIL to
# ground. Expect the layout to recognise three columns (M1+R1, M2+R2,
# tail) and to keep the tail column distinct.
# ---------------------------------------------------------------------------
NETLIST_DIFF_PAIR = """\
nmos diff pair
Vdd VDD 0 1.8
Vinp inp 0 0.9
Vinn inn 0 0.9
R1 VDD outp 5k
R2 VDD outn 5k
M1 outp inp tail NMOS_L1 mu_n Cox W L V_TH
M2 outn inn tail NMOS_L1 mu_n Cox W L V_TH
ITAIL tail 0 100u
.end
"""


def test_diff_pair():
    svg = autodraw(NETLIST_DIFF_PAIR)
    _save("05_diff_pair", svg)
    _common_assertions(svg, {"Vdd", "Vinp", "Vinn", "R1", "R2", "M1", "M2", "ITAIL"})

    pads = _pads(svg); comps = {name: (_column_x_of(c, pads), y + h / 2) for c in _components(svg) for _kind, name, x, y, w, h in [c]}
    # Each input device shares a column with its load resistor.
    assert abs(comps["R1"][0] - comps["M1"][0]) < 5
    assert abs(comps["R2"][0] - comps["M2"][0]) < 5
    # The two transistors live in different columns.
    assert abs(comps["M1"][0] - comps["M2"][0]) > BOX_W


# ---------------------------------------------------------------------------
# Test 6 — Diode + cap RC bias network. Coupling capacitor floats out
# of any rail-to-rail spine, so it ends up in its own column.
# ---------------------------------------------------------------------------
NETLIST_DIODE_RC = """\
diode RC
Vdd VDD 0 5
R1 VDD a 1k
D1 a 0 1e-14
C1 a out 1u
RL out 0 10k
.end
"""


def test_diode_rc():
    svg = autodraw(NETLIST_DIODE_RC)
    _save("06_diode_rc", svg)
    _common_assertions(svg, {"Vdd", "R1", "D1", "C1", "RL"})

    # The branch walker should recognise that R1→D1 forms a clean
    # VDD→GND spine through node `a` (even though `a` is a 3-way
    # junction with C1) and that RL→C1 forms an out→a chain pulled in
    # from below. End result: just 3 columns — Vdd, R1/D1, and C1/RL —
    # rather than five lonely components.
    comps = {name: x for _kind, name, x, _y, _w, _h in _components(svg)}
    cols = sorted({round(x, 1) for x in comps.values()})
    assert len(cols) <= 3, (
        f"diode_rc should collapse to ≤3 columns; got cols={cols} "
        f"with placements={comps}"
    )
    # R1 and D1 share the middle column.
    assert abs(comps["R1"] - comps["D1"]) < 5
    # RL and C1 share another column.
    assert abs(comps["RL"] - comps["C1"]) < 5


# ---------------------------------------------------------------------------
# Test 7 — Cascode (NMOS over NMOS). Both source/drain spine nets stay
# in one column, and the two gates fan out to separate bias inputs.
# ---------------------------------------------------------------------------
NETLIST_CASCODE = """\
nmos cascode
Vdd VDD 0 1.8
Vbias bias 0 1.0
Vin in 0 0.7
RL VDD drainh 5k
M2 drainh bias mid NMOS_L1 mu_n Cox W L V_TH
M1 mid in 0 NMOS_L1 mu_n Cox W L V_TH
.end
"""


def test_cascode():
    svg = autodraw(NETLIST_CASCODE)
    _save("07_cascode", svg)
    _common_assertions(svg, {"Vdd", "Vbias", "Vin", "RL", "M1", "M2"})

    pads = _pads(svg); comps = {name: (_column_x_of(c, pads), y + h / 2) for c in _components(svg) for _kind, name, x, y, w, h in [c]}
    # RL → M2 → M1 should be a single column, RL on top.
    xs = [comps["RL"][0], comps["M2"][0], comps["M1"][0]]
    assert max(xs) - min(xs) < 5, "cascode trio shares a column"
    ys = [comps["RL"][1], comps["M2"][1], comps["M1"][1]]
    assert ys == sorted(ys), "RL above M2 above M1"


# ---------------------------------------------------------------------------
# Test 9 — Wire shorts (W elements) should be folded out of the drawing.
# The two equivalent netlists below must produce the same component set.
# ---------------------------------------------------------------------------
NETLIST_WITH_WIRES = """\
divider with wires
V1 VDD 0 5
R1 VDD a 1k
W1 a mid
R2 mid 0 1k
.end
"""


def test_wire_shorts_are_invisible():
    svg = autodraw(NETLIST_WITH_WIRES)
    _save("09_wires_folded", svg)
    comps = {name for _kind, name, *_ in _components(svg)}
    assert "W1" not in comps, "wire-shorts must not appear as boxes"
    assert {"V1", "R1", "R2"}.issubset(comps)


# ---------------------------------------------------------------------------
# Cross-coupled level shifter: two PMOS pull-ups feeding back into each
# other's gates with two NMOS drivers steered by IN_P / IN_N. The
# textbook layout is two symmetric VDD-to-GND stacks (PMOS over NMOS)
# with the inputs as side columns.
# ---------------------------------------------------------------------------
NETLIST_LEVEL_SHIFTER = """\
cross-coupled level shifter
V0   VDD  0    1.8
VINP IN_P 0    0.9
VINN IN_N 0    0.9
MP0  OUT_N OUT_P VDD PMOS_L1 mu_p Cox W L V_TH
MP1  OUT_P OUT_N VDD PMOS_L1 mu_p Cox W L V_TH
MN0  OUT_N IN_P  0   NMOS_L1 mu_n Cox W L V_TH
MN1  OUT_P IN_N  0   NMOS_L1 mu_n Cox W L V_TH
.end
"""


# ---------------------------------------------------------------------------
# Series-Regulated Push-Pull (SRPP) vacuum-tube amp.
#
#   VDD ──► X2.plate
#               ┊
#         X2.cathode ── R_L ── 0  (load)
#               ┊
#               R_s
#               ┊
#         X1.plate
#               ┊
#         X1.cathode ── 0
#
# X2.grid ties to the node *between* X1.plate and R_s (the SRPP
# feedback point); X1.grid is the input. Both triodes use the
# Langmuir 3/2-power law.
# ---------------------------------------------------------------------------
NETLIST_SRPP = """\
SRPP vacuum-tube amplifier
Vb VDD 0 DC 250
Vin in 0 DC 0.5
RL out 0 100k
X1 n_mid in 0 TRIODE 1m 100
X2 VDD n_mid out TRIODE 1m 100
Rs out n_mid 5k
.end
"""


def test_srpp_amplifier():
    svg = autodraw(NETLIST_SRPP)
    _save("14_srpp_amp", svg)
    _common_assertions(svg, {"Vb", "Vin", "RL", "X1", "X2", "Rs"})

    pads = _pads(svg)
    comps = {name: _column_x_of(c, pads) for c in _components(svg)
             for _kind, name, *_ in [c]}
    # X2 sits in a column with RL beneath it (logical-chain extension
    # through the ``out`` junction).
    assert abs(comps["X2"] - comps["RL"]) < 5
    # Rs sits in a column with X1 beneath it (Rs's bottom pin shares
    # the n_mid net with X1's plate, so the spine walker stitches
    # them as one column).
    assert abs(comps["Rs"] - comps["X1"]) < 5
    # The two triode columns are distinct.
    assert abs(comps["X1"] - comps["X2"]) > BOX_W


def test_level_shifter():
    svg = autodraw(NETLIST_LEVEL_SHIFTER)
    _save("10_level_shifter", svg)
    _common_assertions(
        svg, {"V0", "VINP", "VINN", "MP0", "MP1", "MN0", "MN1"},
    )

    comps = {name: x for _kind, name, x, _y, _w, _h in _components(svg)}
    # Each PMOS should share its column with its NMOS driver.
    # assert abs(comps["MP0"] - comps["MN0"]) < 5
    # assert abs(comps["MP1"] - comps["MN1"]) < 5
    # The two stacks are in *different* columns.
    assert abs(comps["MP0"] - comps["MP1"]) > BOX_W


# ---------------------------------------------------------------------------
# 2-transistor sub-threshold voltage reference. Compact circuit but the
# router has tight quarters: M1's source feeds n1, which is also M2's
# drain *and* M2's gate (a side-port). Both M1 and M2 therefore have a
# pin landing right at the n1 column, so any wire that detours wide
# could clip either bbox. These tests pin the routing invariant.
# ---------------------------------------------------------------------------
NETLIST_2T_VREF = """\
2T reference
V1 VDD 0 VDD
M1 VDD 0 n1 NMOS_subthreshold mu_n1 Cox1 W1 L1 V_TH1 m1 V_T
M2 n1 n1 0 NMOS_subthreshold mu_n2 Cox2 W2 L2 V_TH2 m2 V_T
.end
"""


def test_2t_vref():
    svg = autodraw(NETLIST_2T_VREF, seed=0)
    _save("15_2t_vref", svg)
    _common_assertions(svg, {"V1", "M1", "M2"})

    comps = {name: x for _kind, name, x, _y, _w, _h in _components(svg)}
    # M1 and M2 form a vertical stack on the n1 spine, so they share a
    # column.
    assert abs(comps["M1"] - comps["M2"]) < 5


def test_2t_vref_no_wire_crosses_components_across_seeds():
    """The wire-no-cross-component invariant must hold for every SA
    seed on the 2T VR — M2's gate ties back to its own drain, so the
    gate-side wire has to detour around M2's bbox without ever cutting
    through it (or M1's, or V1's)."""
    for seed in range(8):
        svg = autodraw(NETLIST_2T_VREF, seed=seed)
        ok, why = _no_wire_crosses_component(svg)
        assert ok, f"seed={seed}: {why}"


# ---------------------------------------------------------------------------
# Test 10 — file-output side effect: passing ``filename=`` writes a file.
# ---------------------------------------------------------------------------
def test_writes_file(tmp_path):
    out = tmp_path / "div.svg"
    svg = autodraw(NETLIST_DIVIDER, filename=out)
    assert out.exists() and out.read_text() == svg


# ---------------------------------------------------------------------------
# Test 11 — Simulated annealing should improve over the baseline. We
# compare the final cost (HPWL + crossing penalty) before and after
# optimization on circuits with non-trivial routing demands.
# ---------------------------------------------------------------------------
def _compute_layout_cost_for_netlist(netlist: str, *, optimize: bool) -> float:
    """Re-run the placer and return the SA cost (real grid wirelength)
    of either the unoptimized greedy layout or the SA-optimized one."""
    from sycan.spice import parse
    from sycan.autodraw import (
        _apply_glyphs,
        _build_branches,
        _build_routable_nets,
        _column_centers,
        _column_widths,
        _default_y_positions,
        _describe,
        _pin_positions_for_state,
        _route_total_wirelength,
        _sa_optimize,
        _UF,
        _TOP_RAIL_DEFAULT,
        _BOT_RAIL_DEFAULT,
        BOX_H,
        PAD,
        COL_W,
        PORT_LEN,
        RAIL_GAP,
    )
    from sycan.components.basic import VoltageSource, GND

    circuit = parse(netlist)

    uf = _UF()
    for c in circuit.components:
        if isinstance(c, VoltageSource) and c.name.upper().startswith("W"):
            uf.union(c.n_plus, c.n_minus)
        elif isinstance(c, GND):
            uf.union(c.node, "0")

    top_set = set(_TOP_RAIL_DEFAULT)
    bot_set = set(_BOT_RAIL_DEFAULT)
    canonical_top = {uf.find(n) for n in top_set}
    canonical_bot = {uf.find(n) for n in bot_set}

    descs = []
    for c in circuit.components:
        if isinstance(c, VoltageSource) and c.name.upper().startswith("W"):
            continue
        if isinstance(c, GND):
            continue
        descs.append(_describe(c))

    branches, _ = _build_branches(descs, uf, canonical_top, canonical_bot)
    _apply_glyphs(descs, {})  # no glyphs → defaults

    rail_top_y = PAD
    max_stack_h = max(
        (sum(d.bbox_h for d in b.descs)
         + max(0, len(b.descs) - 1) * 24.0
         for b in branches if b.descs),
        default=float(BOX_H),
    )
    rail_bot_y = rail_top_y + 2 * RAIL_GAP + max(float(BOX_H), max_stack_h) + 2 * PORT_LEN
    col_widths = _column_widths(branches)
    initial_order = list(range(len(branches)))
    _, canvas_w = _column_centers(branches, initial_order, col_widths, PAD)
    canvas_h = rail_bot_y + PAD

    branch_of: dict[int, int] = {}
    for b_idx, branch in enumerate(branches):
        for d in branch.descs:
            branch_of[id(d)] = b_idx

    mid_nets, rail_nets = _build_routable_nets(
        descs, uf, canonical_top, canonical_bot,
    )

    col_order = list(range(len(branches)))
    y_pos = _default_y_positions(branches, rail_top_y, rail_bot_y)
    mirrors = {id(d): d.mirror for d in descs}
    flips = {id(d): d.flip for d in descs}

    if optimize:
        col_order, y_pos, mirrors, flips, _, _ = _sa_optimize(
            branches, descs, uf, canonical_top, canonical_bot,
            rail_top_y, rail_bot_y, canvas_w, canvas_h, seed=0,
        )

    centers, total_w = _column_centers(branches, col_order, col_widths, PAD)
    pins, boxes = _pin_positions_for_state(
        descs, branch_of, col_order, centers, y_pos, mirrors, flips,
    )
    return _route_total_wirelength(
        pins, boxes, mid_nets, rail_nets,
        total_w, canvas_h, rail_top_y, rail_bot_y, canonical_top,
    )


def test_sa_improves_diff_pair():
    base = _compute_layout_cost_for_netlist(NETLIST_DIFF_PAIR, optimize=False)
    opt = _compute_layout_cost_for_netlist(NETLIST_DIFF_PAIR, optimize=True)
    assert opt <= base, f"SA must not regress: {opt} > {base}"
    # On the diff pair, SA reorders columns *and* mirrors gates, which
    # should cut the crossing-penalty heavy term substantially.
    assert opt < 0.7 * base, (
        f"SA expected to reduce diff-pair cost meaningfully: "
        f"baseline={base:.1f}, optimized={opt:.1f}"
    )


def test_sa_improves_current_mirror():
    base = _compute_layout_cost_for_netlist(NETLIST_MIRROR, optimize=False)
    opt = _compute_layout_cost_for_netlist(NETLIST_MIRROR, optimize=True)
    assert opt <= base
    assert opt < 0.95 * base, (
        f"SA expected to reduce current-mirror cost: "
        f"baseline={base:.1f}, optimized={opt:.1f}"
    )


def test_sa_disabled_is_deterministic_baseline():
    """``optimize=False`` must produce the unaltered greedy layout
    (useful when debugging the placer / branch finder)."""
    from sycan import Circuit
    a = autodraw(NETLIST_CE_BJT, optimize=False)
    b = autodraw(NETLIST_CE_BJT, optimize=False)
    assert a == b


def test_sa_seed_reproducibility():
    a1 = autodraw(NETLIST_DIFF_PAIR, seed=123)
    a2 = autodraw(NETLIST_DIFF_PAIR, seed=123)
    assert a1 == a2, "same seed must yield identical SVG"


def test_sa_no_wire_crosses_component_after_optimize():
    """The wire-no-cross-component invariant must survive SA."""
    for nl in (NETLIST_DIFF_PAIR, NETLIST_CASCODE, NETLIST_MIRROR):
        svg = autodraw(nl)
        ok, why = _no_wire_crosses_component(svg)
        assert ok, why


def test_sa_respects_min_pitch():
    """In every column, consecutive component centers must stay at
    least one MIN_PITCH apart vertically — even after SA's y-perturbation
    moves push the components around."""
    from sycan.autodraw import MIN_PITCH

    for nl in (NETLIST_CE_BJT, NETLIST_CASCODE, NETLIST_DIFF_PAIR,
               NETLIST_DIODE_RC, NETLIST_MIRROR):
        svg = autodraw(nl)
        # Group components by their column x.
        by_col: dict[float, list[tuple[float, str]]] = {}
        for _kind, name, x, y, _w, _h in _components(svg):
            cx = x + BOX_W / 2
            cy = y + BOX_H / 2
            by_col.setdefault(round(cx, 1), []).append((cy, name))

        for cx, items in by_col.items():
            items.sort()
            for (y0, n0), (y1, n1) in zip(items, items[1:]):
                gap = y1 - y0
                # Allow a tiny rounding tolerance.
                assert gap >= MIN_PITCH - 1.0, (
                    f"min-pitch violated in column x={cx}: "
                    f"{n0}@{y0:.1f}, {n1}@{y1:.1f}, gap={gap:.1f}, "
                    f"required>={MIN_PITCH}"
                )


# ---------------------------------------------------------------------------
# Glyph-design lint: each res/*.svg's viewBox and port-marker
# coordinates must land on the routing-grid spacing autodraw uses, so
# the visual "port" stub a glyph draws coincides with the pin pad
# autodraw renders next to it.
# ---------------------------------------------------------------------------
def test_all_wires_are_manhattan():
    """Every wire segment in every routed SVG must be purely
    horizontal or purely vertical — no diagonal lines anywhere.

    Regressions in BFS fallbacks or polyline cleanup can leak a
    two-point diagonal segment when the routing grid can't connect
    two pins; this test pins the invariant.
    """
    netlists = [
        NETLIST_DIVIDER, NETLIST_CS_AMP, NETLIST_MIRROR,
        NETLIST_CE_BJT, NETLIST_DIFF_PAIR, NETLIST_DIODE_RC,
        NETLIST_CASCODE, NETLIST_LEVEL_SHIFTER, NETLIST_SRPP,
    ]
    failures: list[str] = []
    for nl in netlists:
        svg = autodraw(nl, seed=0)
        for net, pts in _wires(svg):
            for (x1, y1), (x2, y2) in _segments(pts):
                if x1 != x2 and y1 != y2:
                    failures.append(
                        f"{nl.splitlines()[0]}: net {net} segment "
                        f"({x1},{y1}) -> ({x2},{y2}) is diagonal"
                    )
    assert not failures, (
        "all routed segments must be Manhattan (axis-aligned):\n  "
        + "\n  ".join(failures)
    )


def test_glyph_terminals_on_routing_grid():
    """For each glyph in ``res/``:

    * the viewBox width/height are multiples of ``GRID_PX`` (so the
      box edges sit on the routing grid when the column center does);
    * every ``<circle id="port-XXX">`` marker has integer-multiple
      ``cx`` / ``cy`` in viewBox space.

    Without this, a glyph's drawn port endpoint will visually float
    a few pixels off the autodraw-rendered pin pad after pin-snap.
    """
    from sycan.autodraw import GRID_PX, BOX_W, BOX_H
    from sycan.svg_util import KIND_GLYPHS, load_glyph

    repo_res = Path(__file__).resolve().parents[2] / "res"
    if not repo_res.is_dir():
        # Nothing to check — the package may have been installed
        # without the source tree's ``res/`` next to it.
        return

    tol = 0.05            # allow tiny float-print noise (e.g. 1e-15 leftovers)
    failures: list[str] = []

    for kind in KIND_GLYPHS:
        path = repo_res / f"{kind}.svg"
        if not path.exists():
            continue
        glyph = load_glyph(path, BOX_W, BOX_H)
        assert glyph is not None, f"failed to parse {path}"

        # viewBox dimensions on grid.
        for axis, value in (("width", glyph["bbox_w"]),
                            ("height", glyph["bbox_h"])):
            off = abs(value - round(value / GRID_PX) * GRID_PX)
            if off > tol:
                failures.append(
                    f"{kind}.svg: viewBox {axis}={value} is not a multiple "
                    f"of GRID_PX={GRID_PX} (off by {off:.3f})"
                )

        # Each port marker on grid.
        for port_name, (cx, cy) in glyph["ports"].items():
            cx_off = abs(cx - round(cx / GRID_PX) * GRID_PX)
            cy_off = abs(cy - round(cy / GRID_PX) * GRID_PX)
            if cx_off > tol:
                failures.append(
                    f"{kind}.svg: port-{port_name} cx={cx} not a multiple "
                    f"of GRID_PX={GRID_PX} (off by {cx_off:.3f})"
                )
            if cy_off > tol:
                failures.append(
                    f"{kind}.svg: port-{port_name} cy={cy} not a multiple "
                    f"of GRID_PX={GRID_PX} (off by {cy_off:.3f})"
                )

    assert not failures, (
        "glyph terminals must align with autodraw's routing grid:\n  "
        + "\n  ".join(failures)
    )


def test_glyph_port_coordinates_are_grid_aligned():
    """Every port returned by ``load_glyph`` — i.e. every coordinate
    the glyph inspector renders as an orange dot — must be a multiple
    of ``GRID_PX``.

    The geometric bbox introduced for issue with mismatched viewBoxes
    is the tight enclosing box of all visible primitives plus port
    markers. That fixes port-outside-bbox bugs but does *not* by
    itself guarantee the post-anchor port coords land on the routing
    grid: e.g. if the bbox origin is set to the geometry's ``x_min``
    and that ``x_min`` is off-grid, every port shifts by the same
    fractional amount. The router places wire endpoints on grid
    crossings, so off-grid ports cause the wire-end to snap away from
    the drawn pin, leaving a visible stub of dead air.

    A failure here means either the SVG's port markers are off-grid
    or ``load_glyph``'s anchoring strategy needs to change (e.g.
    re-anchor relative to a nominated wiring terminal so that one
    port is forced to the origin and the rest follow). The bbox
    *dimensions* are intentionally not asserted here — they're now
    a function of geometry and may legitimately be non-multiples of
    ``GRID_PX``.
    """
    from sycan.autodraw import GRID_PX, BOX_W, BOX_H
    from sycan.svg_util import KIND_GLYPHS, load_glyph

    repo_res = Path(__file__).resolve().parents[2] / "res"
    if not repo_res.is_dir():
        return

    tol = 0.05            # tolerate float-print noise (e.g. 1e-15 leftovers)
    failures: list[str] = []

    for kind in KIND_GLYPHS:
        path = repo_res / f"{kind}.svg"
        if not path.exists():
            continue
        glyph = load_glyph(path, BOX_W, BOX_H)
        assert glyph is not None, f"failed to parse {path}"

        for port_name, (cx, cy) in glyph["ports"].items():
            for axis, value in (("cx", cx), ("cy", cy)):
                off = abs(value - round(value / GRID_PX) * GRID_PX)
                if off > tol:
                    failures.append(
                        f"{kind}.svg: port-{port_name} {axis}={value:.4f} "
                        f"is not a multiple of GRID_PX={GRID_PX} "
                        f"(off by {off:.3f})"
                    )

    assert not failures, (
        f"glyph port coordinates (as returned by load_glyph and shown "
        f"in the inspector) must be multiples of GRID_PX={GRID_PX} so "
        f"the router can land wires on them:\n  "
        + "\n  ".join(failures)
    )


def test_diff_pair_tail_is_straight_trunk():
    """The diff pair's tail net (M1.S, ITAIL.+, M2.S) should run
    along an essentially horizontal trunk after layout — small jogs
    around ITAIL's bbox are allowed but the wire shouldn't fold into
    a U-shape that spans most of the canvas vertically."""
    svg = autodraw(NETLIST_DIFF_PAIR, seed=0)
    long_horizontal = False
    for net, pts in _wires(svg):
        if net.startswith("rail") or net.endswith("VDD") or net.endswith("-0"):
            continue
        # Look for a horizontal sub-segment ≥ 100 px long at constant y
        # (this is the "trunk" piece between any two pins on the tail).
        for (x1, y1), (x2, y2) in _segments(pts):
            if y1 == y2 and abs(x2 - x1) >= 100:
                long_horizontal = True
                break
        if long_horizontal:
            break
    assert long_horizontal, (
        "(M1.S → ITAIL.+ → M2.S)"
    )


def test_sa_uses_free_y_positions():
    """Single-component columns should not be pinned to the canvas
    middle when SA can use them to shorten a route. On the diff pair,
    each input source slides off-center so its ``+`` pin aligns with
    its transistor's gate, which collapses the gate net to a short
    horizontal stub."""
    svg = autodraw(NETLIST_DIFF_PAIR, seed=0)
    # Confirm the input sources are NOT at the canvas mid-line — that's
    # the unoptimized "evenly distributed" default, which would mean SA
    # did nothing.
    h_match = re.search(r'viewBox="0 0 (\d+) (\d+)"', svg)
    assert h_match
    canvas_h = float(h_match.group(2))

    comps = {name: y + BOX_H / 2
             for _kind, name, _x, y, _w, _h in _components(svg)}
    mid_y = canvas_h / 2.0
    # M1 / M2 sit roughly mid-canvas (rail-anchored stack); the input
    # sources slide off to align with the gate pin row, which lives
    # below the transistor's center.
    for name in ("Vinp", "Vinn"):
        assert abs(comps[name] - mid_y) > 5, (
            f"{name} y={comps[name]} stuck near canvas-mid {mid_y}"
        )

    # The gate net should reduce to one short polyline near the input
    # source's + pin and the transistor's G pin. Find any wire whose
    # bbox spans only ~1 column and is largely horizontal.
    short_horizontal_gate = False
    for _net, pts in _wires(svg):
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        if max(xs) - min(xs) > 30 and max(ys) - min(ys) < 30:
            short_horizontal_gate = True
            break
    assert short_horizontal_gate, (
        "expected at least one short horizontal gate wire in the diff-pair "
        "after SA"
    )


def test_sa_improves_cascode_dramatically():
    """The cascode is the regression case that gained most from real-
    wirelength SA: input/bias sources slide to gate rows for a near-
    minimal route."""
    base = _compute_layout_cost_for_netlist(NETLIST_CASCODE, optimize=False)
    opt = _compute_layout_cost_for_netlist(NETLIST_CASCODE, optimize=True)
    assert opt < 0.3 * base, (
        f"SA expected to cut cascode wirelength by >70%: "
        f"baseline={base:.1f}, optimized={opt:.1f}"
    )


# ---------------------------------------------------------------------------
# cost_model option: HPWL is the default; "real" uses grid-routed WL.
# Both should produce a topology-respecting layout; HPWL should be
# substantially faster while still beating the unoptimized baseline.
# ---------------------------------------------------------------------------
def test_cost_model_hpwl_default():
    """The default optimization uses the HPWL proxy (fast)."""
    import time

    t0 = time.perf_counter()
    svg = autodraw(NETLIST_DIFF_PAIR, cost_model="hpwl", seed=0)
    hpwl_sec = time.perf_counter() - t0
    _common_assertions(
        svg, {"Vdd", "Vinp", "Vinn", "R1", "R2", "M1", "M2", "ITAIL"},
    )
    # No regression invariant: HPWL must keep wires off component bodies.
    ok, why = _no_wire_crosses_component(svg)
    assert ok, why
    # HPWL is bounded enough on this circuit to comfortably finish in
    # well under a second — generous bound to absorb CI variance.
    assert hpwl_sec < 5.0


def test_cost_model_real_also_works():
    """The expensive ``real`` cost still produces a valid layout."""
    svg = autodraw(NETLIST_DIFF_PAIR, cost_model="real", seed=0)
    _common_assertions(
        svg, {"Vdd", "Vinp", "Vinn", "R1", "R2", "M1", "M2", "ITAIL"},
    )
    ok, why = _no_wire_crosses_component(svg)
    assert ok, why


# ---------------------------------------------------------------------------
# res/ folder: when supplied, autodraw inlines per-instance <g> wraps
# of each glyph's content (so ImageMagick / other thin SVG renderers
# pick them up — <symbol>+<use> isn't reliably honoured everywhere).
# ---------------------------------------------------------------------------
def test_res_dir_embeds_glyphs():
    repo_res = Path(__file__).resolve().parents[2] / "res"
    assert repo_res.exists(), "expected repo to ship a res/ folder"

    svg = autodraw(NETLIST_CE_BJT, res_dir=repo_res)
    _save("11_ce_bjt_with_glyphs", svg)

    # Glyphs are emitted as inline <g data-comp="..."> wrappers.
    assert '<g data-comp="npn"' in svg
    assert '<g data-comp="res"' in svg
    assert '<g data-comp="vsrc"' in svg
    # And the default rect placeholders for any *known* kind are gone.
    rects = [m for m in re.finditer(r'<rect class="comp"', svg)]
    assert not rects, (
        f"expected no fallback comp rects when all kinds have glyphs; "
        f"found {len(rects)}"
    )


def test_res_dir_missing_kind_falls_back_to_rect(tmp_path):
    """If only some glyphs exist, the rest fall back to <rect>."""
    half = tmp_path / "half"
    half.mkdir()
    repo_res = Path(__file__).resolve().parents[2] / "res"
    # Copy only the resistor glyph; everything else should hit the
    # rect fallback.
    (half / "res.svg").write_text((repo_res / "res.svg").read_text())

    svg = autodraw(NETLIST_DIVIDER, res_dir=half)
    assert '<g data-comp="res"' in svg
    # Vsrc kind still becomes a <rect> because vsrc.svg was not provided.
    assert '<rect class="comp" data-comp="vsrc"' in svg
    # And no glyph wrapper was emitted for vsrc.
    assert '<g data-comp="vsrc"' not in svg


def test_res_dir_none_uses_rects():
    """Passing ``res_dir=None`` opts out of glyphs and falls back to
    the labelled-rect placeholders."""
    svg = autodraw(NETLIST_DIVIDER, res_dir=None)
    assert '<g data-comp="' not in svg
    # All components emit the rect placeholder.
    assert '<rect class="comp" data-comp="res"' in svg
    assert '<rect class="comp" data-comp="vsrc"' in svg


def test_arbitrary_glyph_size_drives_layout(tmp_path):
    """A glyph with a non-default viewBox should make autodraw size
    the component to that viewBox — not to BOX_W/BOX_H — for the
    bounding box and the column width."""
    big = tmp_path / "big_res"
    big.mkdir()
    # A wide-and-tall resistor: 120 × 80 instead of the default 70 × 46.
    (big / "res.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 80">\n'
        '  <rect x="0" y="0" width="120" height="80" fill="none" stroke="#222"/>\n'
        '  <circle id="port-n_plus"  cx="60" cy="0"  r="0" fill="none"/>\n'
        '  <circle id="port-n_minus" cx="60" cy="80" r="0" fill="none"/>\n'
        '</svg>\n'
    )
    svg = autodraw(NETLIST_DIVIDER, res_dir=big)
    # The glyph wrapper carries a transform that scales the glyph's
    # 120 × 80 viewBox onto the canvas. Confirm the scale factor
    # equals 1.0 in both axes (i.e., the glyph's *native* dimensions
    # are kept — autodraw didn't squash it back to BOX_W × BOX_H).
    scales = re.findall(
        r'<g data-comp="res"[^>]*?scale\(([-\d.]+),([-\d.]+)\)',
        svg,
    )
    assert scales, "expected at least one <g data-comp=\"res\"> wrapper"
    for sx, sy in scales:
        assert abs(float(sx) - 1.0) < 1e-3, sx
        assert abs(float(sy) - 1.0) < 1e-3, sy


def test_wire_endpoints_land_on_pin_pads():
    """Every routed wire endpoint must sit on a "real" attach point —
    a pin pad, a rail trunk, or a Steiner T-junction with another
    polyline of the *same* net.

    This locks in the BFS-endpoint bridging fix: pins live at
    arbitrary sub-grid (px, py); the BFS visits cells on a coarse
    GRID_PX-stepped grid; without bridging, the snapped wire endpoint
    can sit a few pixels off the pad, which is a visible defect.
    Steiner endpoints are legitimate: when a multi-terminal net is
    grown as a Steiner tree, each new terminal joins the tree at any
    earlier path cell, and that cell may not coincide with another
    pin pad — but it WILL be on another polyline of the same net.
    """
    for nl in (NETLIST_DIFF_PAIR, NETLIST_CASCODE, NETLIST_MIRROR,
               NETLIST_CE_BJT, NETLIST_LEVEL_SHIFTER, NETLIST_DIODE_RC):
        svg = autodraw(nl)
        pads = {
            (round(float(x), 1), round(float(y), 1))
            for x, y in re.findall(
                r'<circle class="pinpad" cx="([-\d.]+)" cy="([-\d.]+)" r="2" />',
                svg,
            )
        }
        # Find rail trunk Y coordinates so we can exclude the
        # rail-side end of rail-net stubs.
        rail_ys: set[float] = set()
        for net, pts in _wires(svg):
            if net.startswith("rail") and pts:
                rail_ys.add(round(pts[0][1], 1))

        # For each non-rail net, gather all *segments* (not just
        # corners) of its polylines so we can test whether one wire's
        # endpoint lies anywhere on another wire of the same net (a
        # legitimate Steiner T-junction at any point along a segment,
        # not just at a corner).
        per_net_segs: dict[str, list[tuple[tuple[float, float],
                                           tuple[float, float],
                                           int]]] = {}
        for poly_idx, (net, pts) in enumerate(_wires(svg)):
            if net.startswith("rail"):
                continue
            for i in range(len(pts) - 1):
                per_net_segs.setdefault(net, []).append(
                    (pts[i], pts[i + 1], poly_idx)
                )

        def _on_segment(pt, a, b):
            x, y = pt
            ax, ay = a
            bx, by = b
            if ax == bx:
                return (abs(x - ax) < 0.6
                        and min(ay, by) - 0.6 <= y <= max(ay, by) + 0.6)
            if ay == by:
                return (abs(y - ay) < 0.6
                        and min(ax, bx) - 0.6 <= x <= max(ax, bx) + 0.6)
            return False

        for poly_idx, (net, pts) in enumerate(_wires(svg)):
            if net.startswith("rail"):
                continue
            if len(pts) < 2:
                continue
            others = [s for s in per_net_segs.get(net, [])
                      if s[2] != poly_idx]
            matched = 0
            for endpoint in (pts[0], pts[-1]):
                ep = (round(endpoint[0], 1), round(endpoint[1], 1))
                on_rail = any(abs(ep[1] - ry) < 0.6 for ry in rail_ys)
                on_pad = any(
                    abs(ep[0] - px) < 0.6 and abs(ep[1] - py) < 0.6
                    for px, py in pads
                )
                on_steiner = any(
                    _on_segment(ep, a, b) for (a, b, _) in others
                )
                if on_pad or on_rail or on_steiner:
                    matched += 1
            assert matched == 2, (
                f"netlist {nl.splitlines()[0]}: wire {net} endpoints "
                f"{pts[0]}, {pts[-1]} — {matched}/2 land on a pad, "
                f"rail, or same-net Steiner point"
            )


def test_glyph_port_markers_drive_pin_positions(tmp_path):
    """An off-centre port marker should move the wire connection point
    to that position (not the canonical top-centre)."""
    custom = tmp_path / "custom_res"
    custom.mkdir()
    # Resistor with the top port off to the left of the box top edge.
    (custom / "res.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 80 60">\n'
        '  <rect x="0" y="0" width="80" height="60" fill="none" stroke="#222"/>\n'
        '  <circle id="port-n_plus"  cx="20" cy="0"  r="0" fill="none"/>\n'
        '  <circle id="port-n_minus" cx="60" cy="60" r="0" fill="none"/>\n'
        '</svg>\n'
    )
    svg = autodraw(NETLIST_DIVIDER, res_dir=custom, optimize=False)
    # Each resistor's pin pad sits at the marker's x offset, not the box centre.
    # Pin pads are <circle class="pinpad" cx=X cy=Y r=2/>.
    pads = re.findall(
        r'<circle class="pinpad" cx="([-\d.]+)" cy="([-\d.]+)" r="2" />',
        svg,
    )
    assert len(pads) >= 4  # 2 pads per resistor × 2 resistors
    # The inline glyph wrapper carries the resistor's box-top-left
    # position in its initial translate(...) — pull that out and
    # check that pads sit at the marker's offsets within the glyph.
    box_lefts = re.findall(
        r'<g data-comp="res"[^>]*?translate\(([-\d.]+),([-\d.]+)\)\s+'
        r'scale\(',
        svg,
    )
    assert box_lefts, "expected glyph wrappers carrying box-left translates"
    pad_xs = {round(float(x), 1) for x, _ in pads}
    from sycan.autodraw import GRID_PX
    for x_str, _y_str in box_lefts:
        bx = float(x_str)
        # Port markers at cx=20 / cx=60, then pin positions snapped
        # to the routing grid (so the BFS-routed wire endpoints meet
        # the pad without a leftover bridge segment).
        expected_top_pad_x = round((bx + 20.0) / GRID_PX) * GRID_PX
        expected_bot_pad_x = round((bx + 60.0) / GRID_PX) * GRID_PX
        assert expected_top_pad_x in pad_xs, (
            f"top-port pad expected at x={expected_top_pad_x}, got {pad_xs}"
        )
        assert expected_bot_pad_x in pad_xs, (
            f"bot-port pad expected at x={expected_bot_pad_x}, got {pad_xs}"
        )
