Getting started
===============

This page walks through installing sycan, building a circuit, and using
the four core solvers — :func:`~sycan.solve_dc`, :func:`~sycan.solve_ac`,
:func:`~sycan.solve_impedance`, and :func:`~sycan.solve_noise` — plus
the schematic renderer :func:`~sycan.autodraw`. Every snippet is
self-contained; copy-paste into a Python REPL and they should run as-is.

Install
-------

sycan needs Python ≥ 3.11 and SymPy. With ``pip``:

.. code-block:: console

   pip install sycan

With `uv <https://docs.astral.sh/uv/>`_:

.. code-block:: console

   uv add sycan

The only hard runtime dependency is ``sympy`` — sycan reaches the CAS
through its own :mod:`sycan.cas` wrapper, so the underlying library can
be swapped via :func:`sycan.cas.select_backend` (sympy is the default
and currently the only implemented backend). The schematic glyphs that
:func:`~sycan.autodraw` uses are bundled inside the wheel, so no
post-install configuration is needed.

Try it without installing
~~~~~~~~~~~~~~~~~~~~~~~~~

The `live REPL <repl/>`_ runs sycan entirely in your browser via
Pyodide — no local install. The page ships preset examples (voltage
divider, RC low-pass, CS amp, noise-cancelling LNA, …); click one and
hit *Run*.

Build a circuit
---------------

There are two equivalent ways to describe a netlist:

**1. The Python API.** Start with a :class:`~sycan.Circuit`, then add
components by name. Symbolic values come from :mod:`sycan.cas` (the
CAS proxy, sympy by default), so any parameter can be a free symbol or
a closed-form expression:

.. code-block:: python

   from sycan import cas as cas
   from sycan import Circuit
   from sycan.components.basic import Resistor, VoltageSource

   Vin, R1_, R2_ = cas.symbols("Vin R1 R2", positive=True)

   c = Circuit("voltage divider")
   c.add(VoltageSource("V1", "in", "0", Vin))
   c.add(Resistor("R1", "in", "out", R1_))
   c.add(Resistor("R2", "out", "0", R2_))

Node ``"0"`` is always ground. Convenience methods
(``c.add_resistor``, ``c.add_vsource``, …) wrap the same constructors
when you don't need a reference to the component object.

**2. A SPICE netlist string.** :func:`~sycan.parse` turns a SPICE
netlist into the same :class:`~sycan.Circuit`. This is the shortest path
when you already have a netlist or want to read one from a file:

.. code-block:: python

   from sycan import parse

   c = parse("""voltage divider
   V1 in 0 Vin
   R1 in out R1
   R2 out 0 R2
   .end
   """)

Both forms produce identical circuits — pick whichever reads better for
the problem. The remaining examples mix the two interchangeably.

DC operating point — :func:`~sycan.solve_dc`
--------------------------------------------

:func:`~sycan.solve_dc` returns a dict mapping each unknown (node
voltages ``V(node)`` and source currents ``I(name)``) to its
closed-form expression. For the divider above:

.. code-block:: python

   from sycan import cas as cas
   from sycan import solve_dc

   sol = solve_dc(c)
   V_out = sol[cas.Symbol("V(out)")]
   print(cas.simplify(V_out))   # R2*Vin / (R1 + R2)

Linear circuits go through symbolic LU. When any component reports
``has_nonlinear`` (MOSFETs, BJTs, diodes), the solver instead calls the
CAS solver (``cas.solve``, where ``sp`` is :mod:`sycan.cas`) on the full
residual system — so transcendental operating points (sub-threshold
MOSFETs, diode equations, …) come out as closed-form expressions when
the backend can solve them.

AC transfer functions — :func:`~sycan.solve_ac`
-----------------------------------------------

The AC solver returns the same shape of dict, but each value is a
function of the Laplace variable ``s``. Setting one of the source
``ac_value`` parameters to ``Vin`` (or any expression) gives the
small-signal transfer function:

.. code-block:: python

   from sycan import cas as cas
   from sycan import parse, solve_ac

   c = parse("""RC low-pass
   V1 in 0 AC Vin
   R1 in out R
   C1 out 0 C
   .end
   """)

   sol = solve_ac(c)
   H = sol[cas.Symbol("V(out)")] / cas.Symbol("Vin")
   print(cas.simplify(H))   # 1 / (C*R*s + 1)

Pass your own ``s`` symbol if you want to share it with downstream
analysis (e.g. polynomial filter prototypes from
:mod:`sycan.polynomials`).

Port impedance — :func:`~sycan.solve_impedance`
-----------------------------------------------

To get the small-signal input or output impedance at a node, mark the
nodes of interest as ports and ask :func:`~sycan.solve_impedance`. The
``termination="auto"`` mode picks an appropriate excitation and
loading automatically:

.. code-block:: python

   from sycan import cas as cas
   from sycan import Circuit, solve_impedance

   mu_n, Cox, W, L, V_TH, lam, R_L = cas.symbols("mu_n Cox W L V_TH lam R_L")
   VDD, V_GS_op, V_DS_op, C_gs = cas.symbols("VDD V_GS_op V_DS_op C_gs")

   c = Circuit()
   c.add_port("P_in",  "gate",  "0", "input")
   c.add_port("P_out", "drain", "0", "output")
   c.add_vsource("Vdd", "VDD", "0", value=VDD, ac_value=0)
   c.add_resistor("RL", "VDD", "drain", R_L)
   c.add_nmos_l1("M1", "drain", "gate", "0",
                 mu_n=mu_n, Cox=Cox, W=W, L=L, V_TH=V_TH, lam=lam,
                 C_gs=C_gs, V_GS_op=V_GS_op, V_DS_op=V_DS_op)

   Z_in  = cas.simplify(solve_impedance(c, "P_in",  termination="auto"))
   Z_out = cas.simplify(solve_impedance(c, "P_out", termination="auto"))
   print(Z_in)    # 1 / (s*C_gs)
   print(Z_out)   # R_L || r_o, in closed form

Noise PSD — :func:`~sycan.solve_noise`
--------------------------------------

Pass an ``output_node`` and any component that owns a noise source
(thermal, shot, flicker — any subclass of :class:`~sycan.NoiseSource`)
contributes its trans-impedance to the output PSD. The classic ``kT/C``
of an RC low-pass is one line:

.. code-block:: python

   from sycan import cas as cas
   from sycan import Circuit, T_kelvin, k_B, solve_noise
   from sycan.components.basic import Capacitor, Resistor, VoltageSource

   R, C, omega = cas.symbols("R C omega", positive=True)

   c = Circuit("kT/C")
   c.add(VoltageSource("V1", "in", "0", value=0, ac_value=0))
   c.add(Resistor("R1", "in", "out", R, include_noise="thermal"))
   c.add(Capacitor("C1", "out", "0", C))

   S_total, per_source = solve_noise(c, "out", simplify=True)
   S_omega = cas.simplify(S_total.subs(cas.Symbol("s"), cas.I * omega))
   power = cas.integrate(S_omega, (omega, 0, cas.oo)) / (2 * cas.pi)
   print(cas.simplify(power))   # k_B*T/C

The returned ``per_source`` dict maps each noise-source name to its
individual PSD — handy when you want to pinpoint which device dominates
the output noise.

Draw a schematic — :func:`~sycan.autodraw`
------------------------------------------

:func:`~sycan.autodraw` accepts the same circuit objects (or a SPICE
netlist string) and returns a self-contained SVG:

.. code-block:: python

   from sycan import autodraw

   svg = autodraw(c, filename="rc_lowpass.svg")
   # svg is also returned as a string — useful in notebooks where you
   # can call IPython.display.SVG(svg).

The placer wraps simulated annealing around the SA + routing pipeline;
when wires would lay collinear on top of one another, it automatically
retries with the next seed in a fixed sequence (up to ``max_retries``
times). See :doc:`autodraw` for the full pipeline and tuning knobs.

What next
---------

* :doc:`autodraw` — the schematic-rendering pipeline, glyph loading,
  and pattern-detect overrides.
* :doc:`api` — full API reference with autosummary tables for every
  module.
* `The REPL <repl/>`_ — preset examples covering filters, low-noise
  amplifiers, voltage references, and S-parameter t-lines.
