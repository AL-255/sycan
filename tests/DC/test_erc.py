"""Electrical rule checker."""
from sycan import Circuit, check_circuit


def test_erc_clean_circuit():
    c = Circuit()
    c.add_vsource("V1", "in", "0", 1)
    c.add_resistor("R1", "in", "out", 1)
    c.add_resistor("R2", "out", "0", 1)
    report = check_circuit(c)
    assert report.ok
    assert report.findings == []


def test_erc_dangling_node():
    c = Circuit()
    c.add_vsource("V1", "in", "0", 1)
    c.add_resistor("R1", "in", "dangling", 1)  # 'dangling' has only one pin
    report = check_circuit(c)
    codes = {f.code for f in report.findings}
    assert "DANGLING_NODE" in codes


def test_erc_duplicate_name():
    c = Circuit()
    c.add_vsource("V1", "in", "0", 1)
    c.add_resistor("R1", "in", "0", 1)
    c.add_resistor("R1", "in", "0", 2)  # duplicate name
    report = check_circuit(c)
    assert not report.ok
    assert any(f.code == "DUPLICATE_NAME" for f in report.errors)


def test_erc_self_short():
    c = Circuit()
    c.add_vsource("V1", "n1", "0", 1)
    c.add_resistor("R1", "n1", "n1", 1)  # both pins on same node
    report = check_circuit(c)
    assert any(f.code == "PIN_SHORT" for f in report.warnings)


def test_erc_island():
    c = Circuit()
    c.add_vsource("V1", "in", "0", 1)
    c.add_resistor("R1", "in", "0", 1)
    # Floating subnetwork
    c.add_resistor("R2", "x", "y", 1)
    report = check_circuit(c)
    assert any(f.code == "ISLAND" for f in report.warnings)
