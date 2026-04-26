SYCAN
=====

**SY**\ mbolic **C**\ ircuit **AN**\ alysis: a SymPy-backed netlist solver
that produces closed-form DC operating points, AC transfer functions,
small-signal impedance, and noise spectral densities. 

.. admonition:: Try it in your browser
   :class: tip

   A live REPL with worked examples runs entirely in the browser via
   Pyodide — no install required.

   👉 `Launch the sycan REPL demo <repl/>`_

.. toctree::
   :maxdepth: 2
   :caption: Contents

   autodraw
   api

Quick example
-------------

.. code-block:: python

   import sympy as sp
   from sycan import Circuit, solve_ac

   c = Circuit()
   R, C, Vin = sp.symbols("R C Vin", positive=True)
   c.add_vsource("V1", "in", "0", ac=Vin)
   c.add_resistor("R1", "in", "out", R)
   c.add_capacitor("C1", "out", "0", C)

   sol = solve_ac(c)
   print(sp.simplify(sol["V_out"] / Vin))   # 1 / (s*R*C + 1)

Indices
-------

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
