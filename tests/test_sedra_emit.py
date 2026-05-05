"""SPICE-parser regression tests for the netlist Sedra emits.

The browser-side editor (docs/sedra) builds netlist lines whose
positional layout has to match what ``sycan.spice.parse`` accepts.
The two used to drift: Sedra emitted only ``Q1 c b e NPN`` while the
parser's ``_require(parts, 8, ...)`` demands the trailing
``IS BF BR``. These tests pin the contract from the parser side, so
any future change to either layer surfaces here.

Each fixture is a representative line that mirrors what Sedra writes
out today (see ``ELEM_TYPES[*].netlist`` in ``docs/sedra/src/glyphs.ts``
and ``defaultParams`` in the same file). We don't try to *solve* the
resulting circuits — only that ``parse`` accepts them and registers
the component with the expected attributes.
"""
from __future__ import annotations

import pytest

from sycan import parse


# ---- two-terminal devices -------------------------------------------

def test_diode_minimal_line_parses():
    # Sedra default: ``D1 a c DMOD`` with no params.
    netlist = """
    D1 a c DMOD
    V1 a 0 1
    R1 c 0 R1
    .end
    """
    circ = parse(netlist)
    diodes = [c for c in circ.components if c.name == "D1"]
    assert len(diodes) == 1


def test_diode_with_symbolic_params_parses():
    # Sedra default with the per-instance symbolic IS placeholder.
    netlist = """
    D1 a c DMOD D1_IS
    V1 a 0 1
    R1 c 0 R1
    .end
    """
    circ = parse(netlist)
    assert any(c.name == "D1" for c in circ.components)


# ---- BJTs (Q) -------------------------------------------------------

@pytest.mark.parametrize("model", ["NPN", "PNP"])
def test_bjt_default_emit_line_parses(model: str):
    # The exact tail Sedra now writes for a freshly-placed Q.
    netlist = f"""
    Q1 c b e {model} Q1_IS Q1_BF Q1_BR
    V1 c 0 1
    V2 b 0 0.7
    R1 e 0 R1
    .end
    """
    circ = parse(netlist)
    qs = [c for c in circ.components if c.name == "Q1"]
    assert len(qs) == 1


def test_bjt_truncated_line_rejected_by_parser():
    # Sanity check on the regression: the *old* Sedra emit ("Q1 c b e
    # NPN" only) MUST be rejected. If this ever starts passing, the
    # parser's _require has loosened and the symbolic-param defaults
    # in Sedra are silently letting through under-specified circuits.
    bad = """
    Q1 c b e NPN
    V1 c 0 1
    V2 b 0 0.7
    R1 e 0 R1
    .end
    """
    with pytest.raises(ValueError):
        parse(bad)


# ---- 3-terminal MOSFETs (M) -----------------------------------------

@pytest.mark.parametrize("model", ["NMOS_L1", "PMOS_L1", "NMOS_3T", "PMOS_3T"])
def test_mosfet_3t_default_emit_line_parses(model: str):
    netlist = f"""
    M1 d g s {model} M1_mu M1_Cox M1_W M1_L M1_VTH
    V1 d 0 1
    V2 g 0 0.7
    R1 s 0 R1
    .end
    """
    circ = parse(netlist)
    assert any(c.name == "M1" for c in circ.components)


def test_mosfet_3t_truncated_rejected():
    bad = """
    M1 d g s NMOS
    V1 d 0 1
    V2 g 0 0.7
    R1 s 0 R1
    .end
    """
    # Parser rejects: model 'NMOS' is unknown AND the line is too
    # short. We only assert it raises — the message is implementation
    # detail.
    with pytest.raises((ValueError, IndexError)):
        parse(bad)


# ---- 4-terminal MOSFETs ---------------------------------------------

@pytest.mark.parametrize("model", ["NMOS_4T", "PMOS_4T"])
def test_mosfet_4t_default_emit_line_parses(model: str):
    netlist = f"""
    M1 d g s b {model} M1_mu M1_Cox M1_W M1_L M1_VTH
    V1 d 0 1
    V2 g 0 0.7
    R1 s 0 R1
    R2 b 0 R2
    .end
    """
    circ = parse(netlist)
    assert any(c.name == "M1" for c in circ.components)


def test_mosfet_4t_with_wrong_model_rejected():
    # Old Sedra wrote "NMOS" instead of "NMOS_4T" for 4T parts; the
    # parser falls into the 3-terminal branch and complains the line
    # is too short (or that the model is unknown).
    bad = """
    M1 d g s b NMOS
    V1 d 0 1
    V2 g 0 0.7
    R1 s 0 R1
    R2 b 0 R2
    .end
    """
    with pytest.raises((ValueError, IndexError)):
        parse(bad)


# ---- Triode (X TRIODE) ----------------------------------------------

def test_triode_default_emit_line_parses():
    netlist = """
    X1 plate grid cathode TRIODE X1_K X1_mu
    V1 plate 0 250
    V2 grid 0 -2
    R1 cathode 0 R1
    .end
    """
    circ = parse(netlist)
    assert any(c.name == "X1" for c in circ.components)


def test_triode_truncated_rejected():
    bad = """
    X1 plate grid cathode TRIODE
    V1 plate 0 250
    V2 grid 0 -2
    R1 cathode 0 R1
    .end
    """
    with pytest.raises((ValueError, IndexError)):
        parse(bad)


# ---- user-overridden numeric params ---------------------------------

def test_user_numeric_params_parse():
    # When the user types numbers into the Params field, parse_value
    # turns them into Float() and the line still parses normally.
    netlist = """
    Q1 c b e NPN 1e-15 100 1 0.026
    V1 c 0 1
    V2 b 0 0.7
    R1 e 0 R1
    .end
    """
    circ = parse(netlist)
    assert any(c.name == "Q1" for c in circ.components)
