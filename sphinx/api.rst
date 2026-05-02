API reference
=============

Common entry points
-------------------

.. list-table::
   :widths: 35 65
   :header-rows: 1

   * - Task
     - API
   * - Build circuits
     - :class:`sycan.Circuit`, :func:`sycan.parse`
   * - Run analyses
     - :func:`sycan.solve_dc`, :func:`sycan.solve_ac`,
       :func:`sycan.solve_impedance`, :func:`sycan.solve_noise`
   * - Draw schematics
     - :func:`sycan.autodraw`
   * - Add components directly
     - :mod:`sycan.components.basic`, :mod:`sycan.components.active`,
       :mod:`sycan.components.rf`

Full module reference
---------------------

.. autosummary::
   :toctree: _autosummary
   :recursive:

   sycan.circuit
   sycan.mna
   sycan.network_params
   sycan.polynomials
   sycan.schematic
   sycan.autodraw
   sycan.autodraw_hacks
   sycan.svg_util
   sycan.spice
   sycan.components.basic
   sycan.components.active
   sycan.components.rf
