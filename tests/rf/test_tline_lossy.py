"""Lossy transmission line tests."""
from sycan import cas as cas

from sycan import Circuit, solve_ac


def test_lossless_tline_unchanged():
    """Lossless TLINE (loss=0 default) gives same AC results."""
    Z0, td = cas.symbols("Z0 td", positive=True)
    c = Circuit("lossless")
    c.add_tline("T1", "in", "0", "out", "0", Z0, td)
    c.add_vsource("Vs", "in", "0", 0, ac_value=1)
    c.add_resistor("RL", "out", "0", Z0)
    sol = solve_ac(c)
    assert cas.Symbol("V(out)") in sol


def test_lossy_tline_ac():
    """Lossy TLINE with loss > 0 includes the loss term."""
    Z0, td, loss = cas.symbols("Z0 td loss", positive=True)
    c = Circuit("lossy")
    c.add_tline("T1", "in", "0", "out", "0", Z0, td, loss=loss)
    c.add_vsource("Vs", "in", "0", 0, ac_value=1)
    c.add_resistor("RL", "out", "0", Z0)
    sol = solve_ac(c)
    Vout = sol[cas.Symbol("V(out)")]
    # loss should appear in the expression
    assert loss in Vout.free_symbols


def test_lossy_tline_dc_unchanged():
    """DC: inner conductor is still a short regardless of loss."""
    from sycan import solve_dc

    c = Circuit("lossy_dc")
    c.add_tline("T1", "in", "0", "out", "0",
                Z0=cas.Symbol("Z0"), td=cas.Symbol("td"),
                loss=0.5)
    c.add_vsource("Vs", "in", "0", value=5)
    c.add_resistor("RL", "out", "0", 1e3)
    sol = solve_dc(c)
    # At DC: V(in) = V(out) = 5
    assert float(sol[cas.Symbol("V(out)")]) == 5.0
