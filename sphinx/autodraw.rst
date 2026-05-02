How autodraw works
==================

:func:`sycan.autodraw` takes a :class:`~sycan.Circuit` (or a SPICE
netlist string) and returns a self-contained SVG schematic. The
algorithm is intentionally schematic — boxes and ports, with optional
per-kind glyphs — so the same placer can drive both quick previews and
the in-browser REPL.

This page walks through the five-stage pipeline that lives in
:mod:`sycan.autodraw`. The terminology below — *spine*, *side port*,
*branch*, *junction* — is used consistently in the code, so this page
also doubles as a reading guide for the source.

Pipeline at a glance
--------------------

.. rubric:: 1. Graph build

Each component is mapped to a **spine** (its high-current path) and a
list of **side ports**. The spine for transistors is Drain-Source for
MOSFETs, Collector-Emitter for BJTs, Plate-Cathode for triodes, and
Anode-Cathode for diodes — the carrier path — so vertical stacks of
these devices come out as straight power-rail columns. Wire-shorts
(SPICE ``W``) and explicit :class:`~sycan.GND` ties are folded into a
union-find on the nets, so any number of aliases for the same node end
up sharing a single canonical name.

.. rubric:: 2. Branch finding

Components are greedily walked from a top rail (``VDD`` / ``VCC`` /
``VPP``) to a bottom rail (``VSS`` / ``VEE`` / ``GND`` / ``"0"``)
through their spines. Each successful walk becomes a vertical column.
The walker runs four phases, in order:

A. Walk **down** from every component touching the top rail through
   *non-junction* spine nets only. A "junction" is a net with more than
   two spine endpoints — extending through one would conflate parallel
   stacks into a single column.
B. Walk **up** from every component touching the bottom rail with the
   same non-junction rule. Components whose walk-up direction hits a
   junction immediately are skipped — phase C decides what to do with
   them.
C. **Junction extension.** A branch ending at a mid junction can be
   extended downward through an unused, rail-bound candidate when *no
   other branch* also ends at that junction (otherwise the parallel
   branches would compete and the junction is best left as a shared
   node — diff-pair tails are the canonical case). The dual rule
   extends branches starting at a junction upward.
D. Anything still unused becomes a one-component column (feedback,
   coupling, controlled sources, etc.).

.. rubric:: 3. Placement

Branch columns are laid out left to right, with the top rail and bottom
rail as horizontal trunks. Per-component glyph dimensions (loaded from
``res/<kind>.svg``) are folded in *before* this step so column widths
and the routing grid see the actual bounding boxes, not the default
rect.

When ``optimize=True`` (the default), a simulated-annealing pass refines:

* the column order,
* per-component y position within a column,
* the side-port mirror (which physical side a side pin sits on),
* and, for components whose spine doesn't touch a rail, the spine flip.

The cost is selected by the ``cost_model`` parameter:

``"hpwl"`` (default, fast)
    Half-perimeter wirelength + a bbox-interleave crossing penalty +
    rail-stub lengths.

``"real"`` (slower, denser)
    The actual rectilinear-routed wirelength, computed via a
    Steiner-tree BFS over a coarse routing grid that knows about
    component bodies. Accounts for routing detours, so the final
    layouts can be tighter on circuits where wires would otherwise be
    forced around blocks.

.. rubric:: 4. Routing

Each remaining net — the side ports, plus rail crossings that didn't
fold into a single branch — is routed on a coarse routing grid.
Component bounding boxes are blocked cells, so wires never cross a
component body. Cells already occupied by a wire incur a small penalty
so later nets prefer fresh space, which keeps clutter down. Turns are
penalised lightly to prefer straight wires. Three-way junctions on the
same net produce a solder dot in the SVG.

The grid search is selectable via the ``router=`` flag on
:func:`~sycan.autodraw`:

* ``"dijkstra"`` *(default)* — uniform-cost Dijkstra. Historical
  behaviour; the search front spreads roughly circularly out from the
  source until it hits any cell of the destination set.
* ``"astar"`` — A\* with an admissible Manhattan-bbox heuristic. Same
  per-step edge cost (``1 + 4·used + clearance + 2·turn``), so the
  routed path costs are identical; only the *number of cells expanded*
  differs. Empirically expands ~3-4× fewer cells than Dijkstra on the
  benchmark fixtures and gives a 0-7 % wall-time speedup at the call
  level (final routing is a small fraction of total ``autodraw()``
  runtime; the SA placement loop dominates). See
  ``docs/ROUTER_BENCHMARK.md`` for numbers.

The SA cost-evaluation grid (used during placement search) keeps its
own BFS / Dijkstra-with-clearance implementation regardless of
``router=``.

.. rubric:: 5. Emit SVG

Components are rendered as labelled rects with port pins, or as the
loaded glyph if one was supplied; wires are emitted as polylines. The
output is intentionally schematic so a downstream renderer can replace
each ``<rect data-comp="...">`` with the actual device glyph.

The component model: spine and side ports
-----------------------------------------

The placer sees every component through a small drawing-time view (the
``_CompDesc`` dataclass). Each desc carries:

* ``spine_top`` / ``spine_bot`` — canonical port names on the
  high-current path.
* ``side_ports`` — every other port (gates, bases, control inputs).
* ``flip`` — the instance was placed with its spine inverted, so
  ``spine_bot`` ends up at the top of the box.
* ``mirror`` — swaps which physical side (left vs right) each side
  port goes on; the SA layer flips this to shorten side-port routes
  across columns.
* ``bbox_w`` / ``bbox_h`` and ``port_offsets`` — the component's
  drawing dimensions and per-port pin positions, default to
  ``BOX_W`` / ``BOX_H`` and the canonical edge fallbacks, but are
  overridden by a glyph's ``viewBox`` when a ``res/<kind>.svg``
  exists.

Polarity-aware orientation
~~~~~~~~~~~~~~~~~~~~~~~~~~

NMOS / NPN / triode / diode / V-source put their canonical "top"
terminal toward the higher rail; PMOS / PNP put source / emitter toward
the higher rail. The placement walker may flip that orientation
per-instance to follow the spine, in which case the port labels swap
with it.

Glyph loading
-------------

If ``res_dir`` (defaulting to the bundled ``<repo>/res/``) contains a
``<kind>.svg`` for a component kind, autodraw loads it and uses its
``viewBox`` as the component's bounding box. Ports the glyph defines
override the canonical edge positions; ports the glyph doesn't define
fall back to the canonical fallback positions on the glyph's bounding
box edge — no ``PORT_LEN`` stub is added, since a glyph is expected to
draw its own pin lines if it wants them. Components whose kinds are
missing from the chosen directory fall back to the labelled rect.

Pass ``res_dir=None`` to disable glyphs entirely and draw labelled
rects for every component.

Tweaking the visual density
---------------------------

A handful of module-level constants in :mod:`sycan.autodraw` set the
visual density:

.. list-table::
   :widths: 25 75
   :header-rows: 1

   * - Constant
     - Meaning
   * - ``COL_W``
     - Column width step.
   * - ``ROW_H``
     - Row height step.
   * - ``BOX_W`` / ``BOX_H``
     - Default rect bounding box (overridden by glyphs).
   * - ``PORT_LEN``
     - Stub length on labelled-rect pins.
   * - ``PAD``
     - Outer canvas padding.
   * - ``RAIL_GAP``
     - Extra space between a rail and the first/last component in a
       column.
   * - ``MIN_GAP``
     - Minimum edge-to-edge clearance between two components stacked
       in the same column.
   * - ``GRID_PX``
     - Routing grid resolution.

Pattern-detect overrides (``autodraw_hacks``)
---------------------------------------------

The pipeline above is intentionally generic — it has no concept of
higher-level circuit idioms like a "diff-pair tail" or a "cross-coupled
latch". For those, the generic cost function gives layouts that are
technically valid but visually wrong: a diff-pair tail wire that folds
into a U-shape, or a cross-coupled gate-drain pair routed as two
parallel zig-zags instead of the textbook X.

:mod:`sycan.autodraw_hacks` collects the targeted overrides for those
idioms. Every hook in the module follows the same shape:

1. **Detect** a specific topological pattern in the netlist.
2. **Override** the placement constraint or the routing for that
   pattern, bypassing the generic stage.

These are intentionally pattern-specific (one-shot overrides, not model
improvements). Adding a new idiom is a new detect-then-override pair in
this module without touching the generic pipeline.

Spine junctions (e.g. the diff-pair tail)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

When two arms terminate at a common spine net and a stem column starts
from it, the trunk wire wants to run horizontally across the boundary
between the arm-bottom row and the stem-top row. SA happily collapses
both pin sets onto the same y, but that lands the trunk on a bbox edge
and forces the router into a deep U-detour.

* :func:`~sycan.autodraw_hacks.detect_spine_junctions` walks every
  branch and finds nets with more than two spine endpoints. Branches
  whose *bottom* pin lands there are recorded as "above" the
  junction; branches whose *top* pin lands there are "below" it.
  Junctions with only one populated side are dropped — there is no
  trunk to clear.
* :func:`~sycan.autodraw_hacks.apply_junction_clearance` enforces a
  ``cross_gap`` (``max(MIN_GAP, 2*PORT_LEN)``) between the meeting
  pin sets. The deficit is split symmetrically — above branches lift,
  below branches drop — in ``GRID_PX``-aligned increments, then the
  caller-supplied ``sweep_branch`` re-sweeps the touched branches so
  they stay inside the rail bounds.

Cross-coupled FET pairs (latches, level shifters, SR flops)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Two FETs whose gates and drains cross-tie produce two nets that the
generic router lays down as parallel zig-zags. The textbook drawing is
an X between the two columns — clean, symmetric, with a single obvious
crossing in the middle.

* :func:`~sycan.autodraw_hacks.detect_cross_coupled_pairs` finds NMOS
  or PMOS pairs where ``M_a.gate == M_b.drain`` *and* ``M_b.gate ==
  M_a.drain``, restricted to layouts where both gates face inward
  toward the column gap (the geometry where an X actually fits).
  Other configurations fall through to the BFS router.
* :func:`~sycan.autodraw_hacks.emit_cross_coupled_x` hand-routes each
  arm as a 3-point polyline ``gate → elbow → drain``, with the elbow
  pinned at ``(opposite_gate_x, drain_y)``. That places the diagonal
  endpoints inside the rectangle spanned by the two gate columns, so
  the X reads symmetric and each arm has a small horizontal stub
  along the FET bottom edge to reach the actual drain pin. This is
  the one place autodraw allows non-Manhattan routing — the visual
  gain is unambiguous.

  The hook bails (returns ``{}``) and lets the BFS take over when:

  - the gates or drains aren't row-aligned (the X would skew),
  - the pin x-ordering doesn't support a clean cross, or
  - any segment (diagonal or stub) would clip a *third* component's
    bbox — autodraw would rather draw ugly Manhattan than thread a
    wire through a transistor.

  Extra terminals on the coupling net (e.g. ``MN1.drain`` for the
  level-shifter ``OUT_P``) are daisy-chained off the drain pin via
  Manhattan stubs.
* :func:`~sycan.autodraw_hacks.cross_coupled_pinned_polylines` is the
  one-call API: detect every cross-coupled pair and return the union
  of their hand-routed polylines, keyed by net.

Function reference
------------------

The full signatures and docstrings for the entry points live in the API
reference:

* :func:`sycan.autodraw.autodraw` — generic pipeline.
* :mod:`sycan.autodraw_hacks` — pattern-detect overrides.

For the in-browser REPL, the page bundles the glyph SVGs under
``/repl/res/`` and patches ``autodraw``'s ``res_dir`` default to that
location so example scripts can call ``autodraw(circuit)`` without
passing a path.
