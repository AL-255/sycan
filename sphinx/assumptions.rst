Assumption engine
=================

Real circuit analysis is full of *informal* statements: "the open-loop
gain is huge", "this resistor swamps the load", "this transistor is in
saturation". The :mod:`sycan.assumptions` module promotes these to
first-class objects that the solver folds into its symbolic answer
and the checker re-verifies after the operating point lands.

Three responsibilities live in one module:

#. **Equation transforms.** Each assumption knows how to fold itself
   into a symbolic expression — typically by taking a
   :func:`~sycan.cas.limit`, substituting a value, or rewriting one
   quantity in terms of another.
#. **Post-solve verification.** Region claims like *"M1 in saturation"*
   are no-ops on the equations, but the checker re-evaluates the
   actual node voltages against the region's defining inequalities and
   reports any device that landed somewhere else.
#. **A single solver entry point.** :func:`sycan.solve` accepts a
   ``mode='dc'``/``mode='ac'`` selector and combines circuit-attached
   assumptions with anything passed inline — DC for a linear circuit
   becomes "build the AC matrix and substitute :math:`s = 0`", which
   matches the textbook *DC = AC at ω → 0* unification.

The four assumption types
-------------------------

.. list-table::
   :header-rows: 1
   :widths: 20 20 60

   * - Class
     - Role
     - When to reach for it
   * - :class:`~sycan.Limit`
     - ``symbol → target``
     - Push a free symbol to its asymptote (e.g. op-amp gain ``A → ∞``,
       parasitic capacitance ``C_p → 0``).
   * - :class:`~sycan.MuchGreater` / :class:`~sycan.MuchLess`
     - ``a >> b`` or ``a << b``
     - Drop a small term in a denominator without choosing a numeric
       value for it.
   * - :class:`~sycan.Approximate`
     - ``symbol ≈ value``
     - Substitute a concrete value for a parameter without taking any
       limit. Same effect as ``expr.subs(...)``, but tracked alongside
       the rest of the design intent.
   * - :class:`~sycan.Region`
     - operating region of a device
     - Declarative bias check. Doesn't change the equations; the
       checker confirms (or refutes) the claim against the solved
       operating point.

Every assumption is a frozen :mod:`dataclasses` instance, so they're
hashable, repr-able, and easy to store in lists or dicts.

Limit — collapsing free symbols
-------------------------------

The classic application is the inverting op-amp. The closed-loop gain
of a real device is a rational function of the open-loop gain ``A``;
asserting ``A → ∞`` reduces it to ``-Rf/Ri``.

.. code-block:: python

   import sympy
   from sycan import Circuit, Limit, cas, solve

   Vin, Ri, Rf = cas.symbols("Vin Ri Rf", positive=True)

   c = Circuit("inv_amp")
   c.add_vsource("V1", "in", "0", Vin)
   c.add_resistor("Ri", "in", "inv", Ri)
   c.add_resistor("Rf", "out", "inv", Rf)
   U1 = c.add_opamp("U1", "0", "inv", "out")    # U1.A is the gain symbol
   c.add_resistor("Rl", "out", "0", 1000)

   sol = solve(c, mode="dc",
               assume=[Limit(U1.A, sympy.oo)],
               simplify=True)
   print(sol[cas.Symbol("V(out)")])    # -Rf*Vin/Ri

The ``assume=`` argument takes a list, so multiple limits compose in
order. ``Limit.apply`` falls back to substitution if sympy can't take
the limit, so a finite ``target`` (e.g. ``Limit(C_p, 0)``) Just Works.

MuchGreater / MuchLess — relative magnitudes
--------------------------------------------

When two free symbols are compared, you don't have to pin either to a
number. ``MuchGreater(big, small)`` rewrites ``small`` as ``ε · big``
and takes ``ε → 0``; if either side is already a bare symbol, the
engine prefers the simpler limit ``big → ∞`` or ``small → 0``.

.. code-block:: python

   from sycan import Circuit, MuchGreater, cas, solve

   Vin, R1, R2 = cas.symbols("Vin R1 R2", positive=True)
   c = Circuit("divider")
   c.add_vsource("V1", "in", "0", Vin)
   c.add_resistor("R1", "in", "out", R1)
   c.add_resistor("R2", "out", "0", R2)

   exact = solve(c, mode="dc")[cas.Symbol("V(out)")]
   # Vin*R2/(R1 + R2)

   # As R1 >> R2 the divider's output collapses to ground.
   asymptote = solve(c, mode="dc",
                     assume=[MuchGreater(R1, R2)],
                     simplify=True)
   print(asymptote[cas.Symbol("V(out)")])    # 0

:class:`~sycan.MuchLess` is a thin alias: ``MuchLess(small, big)`` is
sugar for ``MuchGreater(big, small)``.

Approximate — pin without a limit
---------------------------------

:class:`~sycan.Approximate` is a tracked substitution, useful when one
component value should be fixed but the rest of the design stays
symbolic.

.. code-block:: python

   from sycan import Approximate, cas, solve

   c.assume(Approximate(R_load, 50))    # 50 Ω termination

The only difference vs. ``expr.subs`` is that the substitution lives
on the circuit and shows up wherever assumptions are listed (in
``circuit.assumptions``, in :func:`~sycan.format_check_report`).

Region — declare the operating region, then verify
--------------------------------------------------

A region claim like *"M1 in saturation"* doesn't change the symbolic
equations — the device still stamps its single I-V relation. What it
*does* is record the design intent so the checker can re-verify the
condition once the operating point has been solved.

.. code-block:: python

   from sycan import (
       Circuit, Region, cas,
       check_assumptions, format_check_report, solve_dc,
   )

   c = Circuit("cs_amp")
   c.add_vsource("Vdd", "VDD", "0", cas.Rational(9, 5))
   c.add_vsource("Vin", "g",   "0", cas.Rational(7, 10))
   c.add_resistor("RL", "VDD", "d", 10000)
   c.add_nmos_l1(
       "M1", "d", "g", "0",
       cas.Rational(1, 1000), cas.Rational(1, 500),
       10, 1, cas.Rational(1, 2),
   )

   c.assume(Region("M1", "saturation"))    # the design's intent
   sol = solve_dc(c)
   print(format_check_report(c.check_assumptions(sol)))
   # [OK  ] M1 in saturation

If the same circuit is biased with V_in = 0.3 V (below V_TH), the
checker fires:

.. code-block:: text

   [FAIL] M1 in saturation  — device is cutoff: V_GS_eff=3/10 ≤ V_TH=1/2

The :class:`~sycan.CheckResult` object carries the violating
inequality as ``detail`` and a ``measured`` dict with the computed
``V_GS_eff``, ``V_DS_eff``, ``V_TH``, and overdrive ``V_OV`` so you
can render your own diagnostic.

Recognised regions per device
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Device
     - Regions
   * - MOSFET (any flavour)
     - ``"saturation"``, ``"triode"``, ``"cutoff"``
   * - BJT (NPN / PNP)
     - ``"forward-active"`` (alias ``"active"``), ``"reverse-active"``,
       ``"saturation"``, ``"cutoff"``
   * - Diode
     - ``"forward"``, ``"reverse"``

Polarity is handled automatically — for a PMOS the checker compares
``-V_GS`` against ``V_TH``, and similarly for PNP BJTs.

Attaching vs. passing assumptions
---------------------------------

Two equivalent styles. Pick whichever reads better next to the rest of
the circuit:

.. code-block:: python

   # Attached to the circuit — picked up automatically by solve()
   # and check_assumptions().
   c.assume(Limit(U1.A, sympy.oo))
   c.assume_region("M1", "saturation")

   # Or passed inline — useful for one-off "what if" experiments
   # without mutating the circuit.
   sol = solve(c, mode="dc", assume=[Limit(U1.A, sympy.oo)])

Sugar methods on :class:`~sycan.Circuit` cover the common cases:

* :meth:`~sycan.Circuit.assume_limit(symbol, target)`
* :meth:`~sycan.Circuit.assume_much_greater(big, small)`
* :meth:`~sycan.Circuit.assume_much_less(small, big)`
* :meth:`~sycan.Circuit.assume_region(component_name, region)`

The unified solver
------------------

:func:`sycan.solve` is the single entry point that accepts both modes
and the ``assume=`` list:

.. code-block:: python

   from sycan import solve

   sol_dc = solve(c, mode="dc")             # operating point
   sol_ac = solve(c, mode="ac")             # s-domain transfer
   sol_dc_with_limit = solve(
       c, mode="dc", assume=[Limit(U1.A, sympy.oo)],
   )

For an LTI circuit (no nonlinear devices), ``mode="dc"`` literally
builds the AC matrix and substitutes ``s = 0`` in the closed-form
solution — DC and AC become two queries against the same machinery,
fulfilling the *DC = AC at ω → 0* unification. Circuits containing
:class:`~sycan.NMOS_L1`, :class:`~sycan.BJT`, etc. fall back to the
existing nonlinear ``solve_dc`` path because their stamps don't carry
an LTI small-signal model that can be evaluated at ``s=0``.

Both legacy entry points (:func:`sycan.solve_dc`, :func:`sycan.solve_ac`)
gained an ``assume=`` keyword for the same effect, so existing scripts
can opt in without changing their entry call.

Checking the assumptions
------------------------

After solving, run the checker. It returns a list of
:class:`~sycan.CheckResult` in the order the assumptions were attached:

.. code-block:: python

   from sycan import check_assumptions, format_check_report, violations

   results = check_assumptions(c, sol)         # uses circuit.assumptions
   print(format_check_report(results))

   for v in violations(results):
       print("violated:", v.description)
       print("  why:", v.detail)
       print("  measured:", v.measured)

:func:`~sycan.violations` filters down to the failing cases, and
:func:`~sycan.format_check_report` prints them in a one-line-per-claim
format suitable for tests or terminal logs.

Worked example: amp design with intent baked in
-----------------------------------------------

The pattern that pays off is to *write down* every limit and bias
condition as the circuit is built, then let one ``solve`` /
``check_assumptions`` pair do the rest:

.. code-block:: python

   import sympy
   from sycan import (
       Circuit, Limit, Region, cas,
       check_assumptions, format_check_report, solve,
   )

   Vin, Ri, Rf = cas.symbols("Vin Ri Rf", positive=True)

   c = Circuit("inv_amp_with_intent")
   c.add_vsource("V1", "in", "0", Vin)
   c.add_resistor("Ri", "in", "inv", Ri)
   c.add_resistor("Rf", "out", "inv", Rf)
   U1 = c.add_opamp("U1", "0", "inv", "out")
   c.add_resistor("Rl", "out", "0", 1000)

   # Design intent.
   c.assume(Limit(U1.A, sympy.oo))           # ideal op-amp
   # (no devices to region-check here, but Region claims would go here too)

   sol = solve(c, mode="dc", simplify=True)
   print(sol[cas.Symbol("V(out)")])           # -Rf*Vin/Ri

   results = check_assumptions(c, sol)
   print(format_check_report(results))
   # [OK  ] A_U1 → oo

Try it in the REPL
------------------

Three preset examples in the in-browser
`REPL <repl/?example=assume_ideal_opamp.py>`_ correspond to this page:

* **Ideal op-amp limit** — collapse the inverting amp to its textbook
  closed-loop form.
* **Divider asymptotes** — sweep ``MuchGreater`` both ways across a
  voltage divider.
* **MOSFET region check** — same circuit biased two different ways;
  the checker passes one and flags the other.

See also
--------

* :mod:`sycan.assumptions` — full module reference (auto-generated).
* :func:`sycan.solve`, :func:`sycan.solve_dc`, :func:`sycan.solve_ac` —
  the solver entry points that consume assumptions.
* :meth:`sycan.Circuit.assume`, :meth:`sycan.Circuit.check_assumptions`
  — the per-circuit attachment and verification API.
