from sycan import cas as cas
from sycan import Circuit, solve_transient
from sycan import autodraw

# Natural responses from initial conditions: no sources, only stored
# energy. Capacitor ICs are set with ic= (v0 = V(n+) - V(n-) at t=0-),
# inductor ICs with ic= (i0 flowing n+ -> n- through the inductor).

print("1) Capacitor discharge: R parallel C, cap pre-charged to V0")
R, C, V0 = cas.symbols("R C V0", positive=True)
c1 = Circuit("rc_natural")
c1.add_capacitor("C1", "out", "0", C, ic=V0)
c1.add_resistor("R1", "out", "0", R)

tr1 = solve_transient(c1, outputs=["out"], simplify=True)
print(f"$$v_{{out}}(t) = {cas.latex(tr1.t_solution[cas.Symbol('V(out)')])}$$")
print()

print("2) Inductor de-energising: R-L loop with initial current I0")
L, I0 = cas.symbols("L I0", positive=True)
c2 = Circuit("rl_natural")
c2.add_inductor("L1", "n1", "0", L, ic=I0)
c2.add_resistor("R1", "n1", "0", R)

tr2 = solve_transient(c2, outputs=[cas.Symbol("I(L1)")], simplify=True)
print(f"$$i_L(t) = {cas.latex(tr2.t_solution[cas.Symbol('I(L1)')])}$$")
print()

print("3) Underdamped series RLC, cap pre-charged to V0")
# Numeric element values give a concrete ringing waveform; the
# solver-time initial_conditions map overrides any element ic fields.
c3 = Circuit("rlc_ring")
c3.add_inductor("L1", "top", "mid", 1)
c3.add_resistor("R1", "mid", "out", 1)
c3.add_capacitor("C1", "out", "0", 1, ic=0)
c3.add_vsource("V1", "top", "0", 0)   # shorted loop closes the circuit

tr3 = solve_transient(
    c3, outputs=["out"], simplify=True, initial_conditions={"C1": V0}
)
print(f"$$v_{{out}}(t) = {cas.latex(tr3.t_solution[cas.Symbol('V(out)')])}$$")

autodraw(c1)
