"""Phase-1 symbolic transient tests: zero-IC LTI circuits.

Each test builds a small linear circuit, runs :func:`solve_transient`,
and compares the inverse-Laplace result against the textbook
time-domain expression. ``t`` is created positive by the solver, so
``Heaviside(t)`` factors collapse to 1 in undelayed responses.
"""
from sycan import cas as cas

from sycan import Circuit, solve_transient


def test_rc_lowpass_step():
    """R into C, pulse step: V(out,t) = Vstep·(1 − e^(−t/RC))."""
    R, C, Vstep = cas.symbols("R C Vstep", positive=True)
    c = Circuit("rc_step")
    c.add_vsource("V1", "in", "0", 0,
                  waveform="pulse", v1=0, v2=Vstep, td=0, pw=cas.oo)
    c.add_resistor("R1", "in", "out", R)
    c.add_capacitor("C1", "out", "0", C)

    tran = solve_transient(c, outputs=["out"], simplify=True)
    t = tran.t
    vout = tran.t_solution[cas.Symbol("V(out)")]
    expected = Vstep * (1 - cas.exp(-t / (R * C)))
    assert cas.simplify(vout - expected) == 0


def test_rc_highpass_step():
    """C into R, step input: output decays as Vstep·e^(−t/RC)."""
    R, C, Vstep = cas.symbols("R C Vstep", positive=True)
    c = Circuit("rc_highpass_step")
    c.add_vsource("V1", "in", "0", 0,
                  waveform="pulse", v1=0, v2=Vstep, td=0, pw=cas.oo)
    c.add_capacitor("C1", "in", "out", C)
    c.add_resistor("R1", "out", "0", R)

    tran = solve_transient(c, outputs=["out"], simplify=True)
    t = tran.t
    vout = tran.t_solution[cas.Symbol("V(out)")]
    expected = Vstep * cas.exp(-t / (R * C))
    assert cas.simplify(vout - expected) == 0


def test_rl_step_current():
    """Series R-L driven by a plain DC source (step at t = 0):
    I(L1,t) = (Vstep/R)·(1 − e^(−Rt/L))."""
    R, L, Vstep = cas.symbols("R L Vstep", positive=True)
    c = Circuit("rl_step")
    c.add_vsource("V1", "in", "0", Vstep)
    c.add_resistor("R1", "in", "n1", R)
    c.add_inductor("L1", "n1", "0", L)

    i_sym = cas.Symbol("I(L1)")
    tran = solve_transient(c, outputs=[i_sym], simplify=True)
    t = tran.t
    i_l = tran.t_solution[i_sym]
    expected = (Vstep / R) * (1 - cas.exp(-R * t / L))
    assert cas.simplify(i_l - expected) == 0


def test_sine_source_through_resistor():
    """A sine source across a resistor inverse-transforms back to the
    original sine waveform."""
    A, f, R = cas.symbols("A f R", positive=True)
    c = Circuit("sine_resistor")
    c.add_vsource("V1", "out", "0", 0,
                  waveform="sine", amplitude=A, frequency=f)
    c.add_resistor("R1", "out", "0", R)

    tran = solve_transient(c, outputs=["out"], simplify=True)
    t = tran.t
    vout = tran.t_solution[cas.Symbol("V(out)")]
    expected = A * cas.sin(2 * cas.pi * f * t)
    assert cas.simplify(vout - expected) == 0


def test_pulse_source_preserves_heaviside():
    """Finite delayed pulse through a resistor keeps its delayed
    Heaviside edges in the time domain."""
    v2, td, pw, R = cas.symbols("v2 td pw R", positive=True)
    c = Circuit("pulse_resistor")
    c.add_vsource("V1", "out", "0", 0,
                  waveform="pulse", v1=0, v2=v2, td=td, pw=pw)
    c.add_resistor("R1", "out", "0", R)

    tran = solve_transient(c, outputs=["out"], simplify=True)
    t = tran.t
    vout = tran.t_solution[cas.Symbol("V(out)")]
    assert vout.atoms(cas.Heaviside), "delayed pulse should keep Heaviside"
    expected = v2 * (cas.Heaviside(t - td) - cas.Heaviside(t - td - pw))
    assert cas.simplify(vout - expected) == 0


def test_exp_source_response():
    """Exponential rise through a resistor inverse-transforms back to
    v1 + dV·(1 − e^(−(t−td1)/τ1))·u(t−td1)."""
    v1, v2, tau1, R = cas.symbols("v1 v2 tau1 R", positive=True)
    c = Circuit("exp_resistor")
    c.add_vsource("V1", "out", "0", 0,
                  waveform="exp", v1=v1, v2=v2, td1=0, tau1=tau1)
    c.add_resistor("R1", "out", "0", R)

    tran = solve_transient(c, outputs=["out"], simplify=True)
    t = tran.t
    vout = tran.t_solution[cas.Symbol("V(out)")]
    expected = v1 + (v2 - v1) * (1 - cas.exp(-t / tau1))
    assert cas.simplify(vout - expected) == 0


def test_s_solution_always_available():
    """The raw Laplace-domain solution is returned alongside the
    time-domain one and contains every unknown."""
    R, C, Vstep = cas.symbols("R C Vstep", positive=True)
    c = Circuit("rc_step")
    c.add_vsource("V1", "in", "0", Vstep)
    c.add_resistor("R1", "in", "out", R)
    c.add_capacitor("C1", "out", "0", C)

    tran = solve_transient(c, outputs=["out"])
    s = tran.s
    assert set(tran.s_solution) == {
        cas.Symbol("V(in)"), cas.Symbol("V(out)"), cas.Symbol("I(V1)")
    }
    expected_s = (Vstep / s) / (1 + s * R * C)
    assert cas.simplify(
        tran.s_solution[cas.Symbol("V(out)")] - expected_s
    ) == 0


def test_outputs_filtering():
    """outputs=["out"] restricts t_solution to V(out) only."""
    R, C = cas.symbols("R C", positive=True)
    c = Circuit("rc")
    c.add_vsource("V1", "in", "0", 1)
    c.add_resistor("R1", "in", "out", R)
    c.add_capacitor("C1", "out", "0", C)

    tran = solve_transient(c, outputs=["out"])
    assert list(tran.t_solution) == [cas.Symbol("V(out)")]


def test_default_outputs_transform_everything():
    R, C = cas.symbols("R C", positive=True)
    c = Circuit("rc")
    c.add_vsource("V1", "in", "0", 1)
    c.add_resistor("R1", "in", "out", R)
    c.add_capacitor("C1", "out", "0", C)

    tran = solve_transient(c)
    assert set(tran.t_solution) == set(tran.s_solution)


def test_unknown_output_raises():
    import pytest

    c = Circuit("rc")
    c.add_vsource("V1", "in", "0", 1)
    c.add_resistor("R1", "in", "0", 1)
    with pytest.raises(ValueError, match="not in solution"):
        solve_transient(c, outputs=["nonexistent"])


def test_public_exports():
    from sycan import solve_transient, TransientResult  # noqa: F401
