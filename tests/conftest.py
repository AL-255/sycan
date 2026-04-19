"""pytest plumbing: ``--draw`` renders each testbench's NETLIST to
``tests/DC/diagrams/<module>.tex`` via lcapy (Circuitikz).
"""
from pathlib import Path

import pytest

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
