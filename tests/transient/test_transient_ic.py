"""Phase-2 transient tests: capacitor / inductor initial conditions.

Natural-response circuits verify both the IC stamp values and their
signs: capacitor polarity is ``v0 = V(n_plus) − V(n_minus)``, inductor
initial current is positive flowing ``n_plus → n_minus`` (the same
direction as the branch unknown ``I(name)``).
"""
import pytest

from sycan import cas as cas

from sycan import Circuit, solve_transient


def test_capacitor_natural_response():
    """R ∥ C with the cap charged to V0: V(out,t) = V0·e^(−t/RC)."""
    R, C, V0 = cas.symbols("R C V0", positive=True)
    c = Circuit("rc_natural")
    c.add_capacitor("C1", "out", "0", C, ic=V0)
    c.add_resistor("R1", "out", "0", R)

    tran = solve_transient(c, outputs=["out"], simplify=True)
    t = tran.t
    vout = tran.t_solution[cas.Symbol("V(out)")]
    expected = V0 * cas.exp(-t / (R * C))
    assert cas.simplify(vout - expected) == 0


def test_capacitor_ic_polarity():
    """Flipping the capacitor terminals flips the response sign."""
    R, C, V0 = cas.symbols("R C V0", positive=True)
    c = Circuit("rc_natural_flipped")
    c.add_capacitor("C1", "0", "out", C, ic=V0)  # v0 = V(0) − V(out)
    c.add_resistor("R1", "out", "0", R)

    tran = solve_transient(c, outputs=["out"], simplify=True)
    vout = tran.t_solution[cas.Symbol("V(out)")]
    expected = -V0 * cas.exp(-tran.t / (R * C))
    assert cas.simplify(vout - expected) == 0


def test_inductor_natural_response():
    """R-L loop with initial current I0: I(L1,t) = I0·e^(−Rt/L)."""
    R, L, I0 = cas.symbols("R L I0", positive=True)
    c = Circuit("rl_natural")
    c.add_inductor("L1", "n1", "0", L, ic=I0)
    c.add_resistor("R1", "n1", "0", R)

    i_sym = cas.Symbol("I(L1)")
    tran = solve_transient(c, outputs=[i_sym], simplify=True)
    t = tran.t
    i_l = tran.t_solution[i_sym]
    expected = I0 * cas.exp(-R * t / L)
    assert cas.simplify(i_l - expected) == 0


def test_solver_override_wins_over_element_ic():
    R, C, Va, Vb = cas.symbols("R C Va Vb", positive=True)
    c = Circuit("rc_override")
    c.add_capacitor("C1", "out", "0", C, ic=Va)
    c.add_resistor("R1", "out", "0", R)

    tran = solve_transient(
        c, outputs=["out"], simplify=True, initial_conditions={"C1": Vb}
    )
    vout = tran.t_solution[cas.Symbol("V(out)")]
    expected = Vb * cas.exp(-tran.t / (R * C))
    assert cas.simplify(vout - expected) == 0
    assert not vout.has(Va)


def test_ic_with_step_source():
    """Pre-charged cap driven by a step settles from V0 to Vstep."""
    R, C, V0, Vstep = cas.symbols("R C V0 Vstep", positive=True)
    c = Circuit("rc_step_ic")
    c.add_vsource("V1", "in", "0", Vstep)
    c.add_resistor("R1", "in", "out", R)
    c.add_capacitor("C1", "out", "0", C, ic=V0)

    tran = solve_transient(c, outputs=["out"], simplify=True)
    t = tran.t
    vout = tran.t_solution[cas.Symbol("V(out)")]
    expected = Vstep + (V0 - Vstep) * cas.exp(-t / (R * C))
    assert cas.simplify(vout - expected) == 0


def test_unknown_ic_name_raises():
    c = Circuit("rc")
    c.add_vsource("V1", "in", "0", 1)
    c.add_resistor("R1", "in", "0", 1)
    with pytest.raises(ValueError, match="unknown component"):
        solve_transient(c, initial_conditions={"C9": 1})


def test_ic_on_non_storage_component_raises():
    c = Circuit("rc")
    c.add_vsource("V1", "in", "0", 1)
    c.add_resistor("R1", "in", "0", 1)
    with pytest.raises(ValueError, match="capacitors and inductors"):
        solve_transient(c, initial_conditions={"R1": 1})


def test_ic_ignored_outside_tran_mode():
    """build_mna rejects initial_conditions for non-tran modes."""
    from sycan import build_mna

    c = Circuit("rc")
    c.add_vsource("V1", "in", "0", 1)
    c.add_resistor("R1", "in", "0", 1)
    with pytest.raises(ValueError, match="mode='tran'"):
        build_mna(c, mode="ac", initial_conditions={"C1": 1})


def test_element_ic_does_not_affect_ac():
    """The ic field must not leak into AC analysis."""
    from sycan import solve_ac

    R, C, V0, Vin = cas.symbols("R C V0 Vin", positive=True)
    s = cas.Symbol("s")
    c = Circuit("rc_ac")
    c.add_vsource("V1", "in", "0", 0, ac_value=Vin)
    c.add_resistor("R1", "in", "out", R)
    c.add_capacitor("C1", "out", "0", C, ic=V0)

    sol = solve_ac(c, s=s)
    vout = sol[cas.Symbol("V(out)")]
    assert not vout.has(V0)
    assert cas.simplify(vout - Vin / (1 + s * R * C)) == 0
