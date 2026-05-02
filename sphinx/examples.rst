Examples
========

These snippets are intentionally small. They show the common shape of a
sycan workflow: build or parse a circuit, run one solver, then simplify
or substitute values with :mod:`sycan.cas`.

DC divider with numeric substitution
------------------------------------

Use :func:`sycan.solve_dc` when all independent sources should use their
DC value.

.. code-block:: python

   from sycan import cas as cas
   from sycan import parse, solve_dc

   c = parse("""voltage divider
   V1 in 0 Vin
   R1 in out R1
   R2 out 0 R2
   .end
   """)

   Vin, R1, R2 = cas.symbols("Vin R1 R2")
   sol = solve_dc(c)
   gain = cas.simplify(sol[cas.Symbol("V(out)")] / Vin)

   print(gain)                              # R2/(R1 + R2)
   print(gain.subs({R1: 9000, R2: 1000}))   # 1/10

Second-order low-pass transfer
------------------------------

Use an ``AC`` source value to drive small-signal analysis in the
Laplace domain.

.. code-block:: python

   from sycan import cas as cas
   from sycan import parse, solve_ac

   c = parse("""series RLC low-pass
   V1 in 0 AC Vin
   L1 in mid L
   R1 mid out R
   C1 out 0 C
   .end
   """)

   sol = solve_ac(c)
   H = cas.factor(sol[cas.Symbol("V(out)")] / cas.Symbol("Vin"))

   print(H)   # 1/(C*L*s**2 + C*R*s + 1)

Input impedance of a loaded node
--------------------------------

Mark the node with a :class:`sycan.Port`, then ask
:func:`sycan.solve_impedance` for that named port.

.. code-block:: python

   from sycan import cas as cas
   from sycan import Circuit, solve_impedance

   s, R, C = cas.symbols("s R C")

   c = Circuit("cap load")
   c.add_port("P_in", "in", "0", "input")
   c.add_resistor("R1", "in", "out", R)
   c.add_capacitor("C1", "out", "0", C)

   Z_in = cas.simplify(solve_impedance(c, "P_in", s=s))
   print(Z_in)   # R + 1/(C*s)

Render a quick schematic
------------------------

:func:`sycan.autodraw` accepts the same SPICE-style netlist strings as
:func:`sycan.parse` and returns the SVG as a string.

.. code-block:: python

   from sycan import autodraw

   svg = autodraw("""divider
   V1 in 0 Vin
   R1 in out R1
   R2 out 0 R2
   .end
   """, filename="divider.svg")

   print(svg.startswith("<svg"))   # True
