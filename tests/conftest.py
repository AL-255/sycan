"""pytest plumbing: ``--draw`` renders each testbench's NETLIST to
``tests/DC/diagrams/<module>.tex`` via lcapy (Circuitikz).

Set ``SYCAN_CAS_BACKEND`` before invoking pytest to run the suite under
a non-default CAS backend (e.g. ``SYCAN_CAS_BACKEND=symengine``). The
selection happens at conftest import time so every later sycan import
sees the chosen backend.
"""
import os
from pathlib import Path

import pytest

_backend = os.environ.get("SYCAN_CAS_BACKEND")
if _backend:
    from sycan import cas as _cas
    _cas.select_backend(_backend)


# Tests known to fail or hang under non-sympy backends. Each entry is a
# substring matched against the test's nodeid (``file::test``); the
# matching tests are skipped. The reasons are CAS-side, not bugs in
# sycan: see docs/BE_PORT_STATUS.md.
_BACKEND_SKIPS: dict[str, list[tuple[str, str]]] = {
    "symengine": [
        # Substitution into LU output yields ``zoo`` because symengine
        # does not auto-simplify ``-1 / (C·s·(-R - 1/(C·s)))`` to
        # ``1/(R·C·s + 1)`` before s = 0 is plugged in.
        ("tests/AC/test_rc_lowpass.py::test_rc_lowpass_dc_limit",
         "symengine: LU result needs canonicalisation before subs(s=0)"),
        # Common-gate AC tests compare specific symbolic forms that
        # sympy's auto-simplify produces and even ``Basic.simplify()``
        # leaves untouched on the symengine side.
        ("tests/blocks/test_common_gate.py::test_cg_ac_voltage_gain",
         "symengine: representation diverges from sympy"),
        ("tests/blocks/test_common_gate.py::test_cg_input_impedance",
         "symengine: representation diverges from sympy"),
        ("tests/blocks/test_common_gate.py::test_cg_output_impedance",
         "symengine: representation diverges from sympy"),
        # Test uses ``.rewrite(sp.log)`` which is sympy-only.
        ("tests/DC/test_bandgap_opamp_pmos.py::test_bandgap_ptat_loop_in_saturation_limit",
         "symengine: Expr.rewrite is sympy-only"),
        # Headroom assertions about closed-form bounds compare specific
        # Min/Max forms that need sympy-side normalisation.
        ("tests/DC/test_headroom.py::test_resistor_load_cs_amp_yields_closed_form_interval",
         "symengine: Min/Max representation diverges"),
        ("tests/DC/test_headroom.py::test_op_point_injection_skips_sp_solve",
         "symengine: Min/Max representation diverges"),
        # Symengine's LUsolve produces an enormous unsimplified form for
        # the four-resistor bridge; sympy.simplify can't close it in
        # reasonable time. Both tests in the file go through solve_dc
        # whose default simplify=True triggers the same blow-up.
        ("tests/DC/test_wheatstone.py::",
         "symengine: LU result is too large for simplify to close"),
        # Same shape — heavy SRPP transfer function, simplify
        # bridge takes too long after symengine's raw LU output.
        ("tests/blocks/test_srpp_amp.py::test_srpp_optimal_load_for_distortion_cancellation",
         "symengine: heavy LU expression, simplify too slow"),
    ],
}


def pytest_collection_modifyitems(config, items):
    skips = _BACKEND_SKIPS.get(_backend or "", [])
    if not skips:
        return
    for item in items:
        for needle, reason in skips:
            if needle in item.nodeid:
                item.add_marker(pytest.mark.skip(reason=reason))
                break

DIAGRAM_DIR = Path(__file__).parent / "diagrams"


def pytest_addoption(parser):
    parser.addoption(
        "--draw",
        action="store_true",
        default=False,
        help="generate a Circuitikz schematic for each testbench's NETLIST",
    )


@pytest.fixture(scope="module", autouse=True)
def _draw_netlist(request):
    if not request.config.getoption("--draw"):
        return
    netlist = getattr(request.module, "NETLIST", None)
    if netlist is None:
        return
    from sycan.schematic import draw, render_png

    test_file = Path(request.module.__file__)
    out = test_file.parent / "diagrams" / f"{test_file.stem}.tex"
    draw(netlist, out)
    render_png(out)
