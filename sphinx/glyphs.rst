Component glyphs
================

Every component class drawn by :func:`~sycan.autodraw` looks up a glyph
by *kind* (an internal short name set in :func:`sycan.autodraw._describe`).
If ``res/<kind>.svg`` exists it overrides the default labelled rect; the
glyph's ``viewBox`` becomes the component's bounding box and any element
tagged ``id="port-<port>"`` becomes the canonical pin position. See
:doc:`autodraw` for how those overrides feed back into placement.

The table below lists every shipped glyph, the SymPy classes that map to
it, and the spine / side-port layout. Click a glyph file (or open the
in-browser `glyph inspector <repl/glyph-inspector.html>`_, also bundled
with the REPL) to inspect its raw SVG.

.. list-table::
   :widths: 12 18 30 30 10
   :header-rows: 1
   :class: glyph-table

   * - Kind
     - Glyph
     - Components
     - Ports (spine top / bot · sides)
     - File

   * - ``nmos``
     - .. image:: ../res/nmos.svg
          :alt: NMOS glyph
          :height: 64
     - :class:`~sycan.NMOS_L1`,
       :class:`~sycan.NMOS_subthreshold`,
       :class:`~sycan.NMOS_3T`
     - drain / source · gate
     - ``res/nmos.svg``

   * - ``nmos_4t``
     - .. image:: ../res/nmos_4t.svg
          :alt: NMOS (body-aware) glyph
          :height: 64
     - 4-terminal :class:`~sycan.components.active.NMOS_4T`
       (body pin exposed)
     - drain / source · gate, bulk
     - ``res/nmos_4t.svg``

   * - ``pmos``
     - .. image:: ../res/pmos.svg
          :alt: PMOS glyph
          :height: 64
     - :class:`~sycan.PMOS_L1`,
       :class:`~sycan.PMOS_subthreshold`,
       :class:`~sycan.PMOS_3T`
     - source / drain · gate
     - ``res/pmos.svg``

   * - ``pmos_4t``
     - .. image:: ../res/pmos_4t.svg
          :alt: PMOS (body-aware) glyph
          :height: 64
     - 4-terminal :class:`~sycan.components.active.PMOS_4T`
     - source / drain · gate, bulk
     - ``res/pmos_4t.svg``

   * - ``npn``
     - .. image:: ../res/npn.svg
          :alt: NPN BJT glyph
          :height: 64
     - :class:`~sycan.BJT` with ``polarity="NPN"``
     - collector / emitter · base
     - ``res/npn.svg``

   * - ``pnp``
     - .. image:: ../res/pnp.svg
          :alt: PNP BJT glyph
          :height: 64
     - :class:`~sycan.BJT` with ``polarity="PNP"``
     - emitter / collector · base
     - ``res/pnp.svg``

   * - ``triode``
     - .. image:: ../res/triode.svg
          :alt: Triode glyph
          :height: 80
     - :class:`~sycan.Triode`
     - plate / cathode · grid
     - ``res/triode.svg``

   * - ``diode``
     - .. image:: ../res/diode.svg
          :alt: Diode glyph
          :height: 64
     - :class:`~sycan.Diode`
     - anode / cathode
     - ``res/diode.svg``

   * - ``vsrc``
     - .. image:: ../res/vsrc.svg
          :alt: Voltage source glyph
          :height: 64
     - :class:`~sycan.VoltageSource`
     - n_plus / n_minus
     - ``res/vsrc.svg``

   * - ``isrc``
     - .. image:: ../res/isrc.svg
          :alt: Current source glyph
          :height: 64
     - :class:`~sycan.CurrentSource`
     - n_plus / n_minus
     - ``res/isrc.svg``

   * - ``res``
     - .. image:: ../res/res.svg
          :alt: Resistor glyph
          :height: 64
     - :class:`~sycan.Resistor`
     - n_plus / n_minus
     - ``res/res.svg``

   * - ``ind``
     - .. image:: ../res/ind.svg
          :alt: Inductor glyph
          :height: 64
     - :class:`~sycan.Inductor`
     - n_plus / n_minus
     - ``res/ind.svg``

   * - ``cap``
     - .. image:: ../res/cap.svg
          :alt: Capacitor glyph
          :height: 64
     - :class:`~sycan.Capacitor`
     - n_plus / n_minus
     - ``res/cap.svg``

   * - ``ccsrc``
     - .. image:: ../res/ccsrc.svg
          :alt: Controlled-source glyph
          :height: 64
     - :class:`~sycan.VCVS`, :class:`~sycan.VCCS`,
       :class:`~sycan.CCCS`, :class:`~sycan.CCVS`
     - n_plus / n_minus · nc_plus, nc_minus *(or)* ctrl
     - ``res/ccsrc.svg``

   * - ``gnd``
     - .. image:: ../res/gnd.svg
          :alt: Ground glyph
          :height: 40
     - :class:`~sycan.GND`
     - node (drawn as a single rail-tie pin)
     - ``res/gnd.svg``

The two kinds that do not ship a bundled SVG are ``port`` (input/output
markers, drawn directly by the SVG emitter rather than as a glyph) and
``tline`` (transmission line — falls back to the labelled rect, which
lets the box size scale with the line's length symbol).

Adding a custom glyph
---------------------

Any kind in the list above can be replaced by dropping a new SVG into a
directory and pointing :func:`~sycan.autodraw` at it:

.. code-block:: python

   from sycan import autodraw

   svg = autodraw(circuit, res_dir="my_glyphs/")

Two conventions the loader expects:

1. **Filename = kind.** Save the file as ``<kind>.svg`` (e.g.
   ``my_glyphs/nmos.svg``); the loader picks it up automatically.
2. **Pin markers = ``id="port-<port>"``.** Each pin terminal must carry
   an ``id`` of the form ``port-drain``, ``port-gate``, etc. Coordinates
   are read from whichever attribute the element exposes
   (``cx``/``cy`` for a ``<circle>``, ``x``/``y`` for a ``<rect>``, the
   first point of a ``<polyline>``, …). Ports that the glyph omits fall
   back to the canonical edge positions on the bounding box.

The bounding box defaults to the SVG ``viewBox``; if absent, the
geometric bbox of the drawing primitives + port markers is used and
snapped to the routing grid. See :func:`sycan.svg_util.load_glyph` for
the exact snap rules.

Pass ``res_dir=None`` to :func:`~sycan.autodraw` to disable glyph
loading entirely and fall back to labelled rects for every component.
