"""PULSE and EXP waveform source tests."""
from sycan import cas as cas

from sycan import Circuit, solve_ac


def test_vsource_pulse_step():
    """Pulse step (td=0, pw=oo) → v2/s in Laplace."""
    v1, v2, R = cas.symbols("v1 v2 R", positive=True)
    s = cas.Symbol("s")
    c = Circuit("pulse_test")
    c.add_vsource("V1", "in", "0", 0,
                  waveform="pulse", v1=v1, v2=v2, td=0, pw=cas.oo)
    c.add_resistor("R1", "in", "0", R)
    sol = solve_ac(c)
    V_in = cas.simplify(sol[cas.Symbol("V(in)")])
    # Step: v1/s + (v2-v1)*(1 - exp(-oo*s)) / s = v2/s
    expected = v2 / s
    assert cas.simplify(V_in - expected) == 0


def test_vsource_pulse_delayed():
    """Pulse with delay td."""
    v1, v2, td, R = cas.symbols("v1 v2 td R", positive=True)
    s = cas.Symbol("s")
    c = Circuit("pulse_delay")
    c.add_vsource("V1", "in", "0", 0,
                  waveform="pulse", v1=v1, v2=v2, td=td, pw=cas.oo)
    c.add_resistor("R1", "in", "0", R)
    sol = solve_ac(c)
    V_in = cas.simplify(sol[cas.Symbol("V(in)")])
    expected = v1 / s + (v2 - v1) * cas.exp(-s * td) / s
    assert cas.simplify(V_in - expected) == 0


def test_vsource_pulse_dc():
    """Pulse source DC is v1."""
    from sycan import solve_dc

    c = Circuit("pulse_dc")
    c.add_vsource("V1", "in", "0", 0,
                  waveform="pulse", v1=5, v2=10, td=1e-3)
    c.add_resistor("R1", "in", "0", 1e3)
    sol = solve_dc(c)
    assert float(sol[cas.Symbol("V(in)")]) == 5.0


def test_vsource_exp_rise():
    """Single exponential rise."""
    v1, v2, td1, tau1 = cas.symbols("v1 v2 td1 tau1", positive=True)
    s = cas.Symbol("s")
    R = cas.Symbol("R", positive=True)
    c = Circuit("exp_test")
    c.add_vsource("V1", "in", "0", 0,
                  waveform="exp", v1=v1, v2=v2, td1=td1, tau1=tau1)
    c.add_resistor("R1", "in", "0", R)
    sol = solve_ac(c)
    V_in = cas.simplify(sol[cas.Symbol("V(in)")])
    dV = v2 - v1
    expected = v1 / s + dV * cas.exp(-s * td1) / (s * (1 + s * tau1))
    assert cas.simplify(V_in - expected) == 0


def test_vsource_exp_dc():
    """EXP source DC is v1."""
    from sycan import solve_dc

    c = Circuit("exp_dc")
    c.add_vsource("V1", "in", "0", 0,
                  waveform="exp", v1=5, v2=10, td1=1e-3, tau1=1e-6)
    c.add_resistor("R1", "in", "0", 1e3)
    sol = solve_dc(c)
    assert float(sol[cas.Symbol("V(in)")]) == 5.0


def test_isource_pulse():
    """Current source with pulse waveform."""
    v1, v2, R = cas.symbols("v1 v2 R", positive=True)
    s = cas.Symbol("s")
    c = Circuit("ipulse")
    c.add_isource("I1", "0", "n", 0,
                  waveform="pulse", v1=v1, v2=v2, td=0, pw=cas.oo)
    c.add_resistor("R1", "n", "0", R)
    sol = solve_ac(c)
    Vn = cas.simplify(sol[cas.Symbol("V(n)")])
    expected = v2 * R / s
    assert cas.simplify(Vn - expected) == 0


def test_vsource_exp_with_fall():
    """Exponential pulse with rise and fall (td2, tau2)."""
    v1, v2, td1, tau1, td2, tau2 = cas.symbols("v1 v2 td1 tau1 td2 tau2",
                                                 positive=True)
    s = cas.Symbol("s")
    c = Circuit("exp_fall")
    c.add_vsource("V1", "in", "0", 0,
                  waveform="exp", v1=v1, v2=v2,
                  td1=td1, tau1=tau1, td2=td2, tau2=tau2)
    c.add_resistor("R1", "in", "0", 1)
    sol = solve_ac(c)
    V_in = cas.simplify(sol[cas.Symbol("V(in)")])
    dV = v2 - v1
    expected = (
        v1 / s
        + dV * cas.exp(-s * td1) / (s * (1 + s * tau1))
        - dV * cas.exp(-s * td2) / (s * (1 + s * tau2))
    )
    assert cas.simplify(V_in - expected) == 0
