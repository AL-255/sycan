"""SPICE parsing / writing of transient source specs and C/L ``IC=``.

Parser tests are separate from solver tests: they assert the mapped
component fields (and round-trips through ``to_spice``), not the
transient solutions themselves — except one end-to-end RC-step check.
"""
import pytest

from sycan import cas as cas

from sycan import parse, solve_transient, to_spice


def _only_source(circuit):
    from sycan.components.basic.voltage_source import VoltageSource
    from sycan.components.basic.current_source import CurrentSource

    for comp in circuit.components:
        if isinstance(comp, (VoltageSource, CurrentSource)):
            return comp
    raise AssertionError("no source found")


# ---------------------------------------------------------------------------
# SIN
# ---------------------------------------------------------------------------

def test_parse_sin_basic():
    c = parse("""t
V1 in 0 SIN(0 5 1k)
R1 in 0 1k
.end
""")
    src = _only_source(c)
    assert src.waveform == "sine"
    assert src.amplitude == 5
    assert src.frequency == 1000
    assert src.phase == 0
    assert src.value == 0


def test_parse_sin_offset_becomes_value():
    c = parse("""t
V1 in 0 SIN(2 5 1k)
R1 in 0 1k
.end
""")
    src = _only_source(c)
    assert src.value == 2


def test_parse_sin_phase_degrees_to_radians():
    c = parse("""t
V1 in 0 SIN(0 5 1k 0 0 90)
R1 in 0 1k
.end
""")
    src = _only_source(c)
    assert cas.simplify(src.phase - cas.pi / 2) == 0


def test_parse_sin_symbolic_args():
    c = parse("""t
V1 in 0 SIN(0 VA FREQ)
R1 in 0 R
.end
""")
    src = _only_source(c)
    assert src.amplitude == cas.Symbol("VA")
    assert src.frequency == cas.Symbol("FREQ")


def test_parse_sin_rejects_delay_and_damping():
    with pytest.raises(ValueError, match="td.*theta|delay"):
        parse("""t
V1 in 0 SIN(0 5 1k 1u)
R1 in 0 1k
.end
""")
    with pytest.raises(ValueError, match="damping|theta"):
        parse("""t
V1 in 0 SIN(0 5 1k 0 100)
R1 in 0 1k
.end
""")


def test_parse_sin_explicit_dc_wins_over_offset():
    c = parse("""t
V1 in 0 DC 3 SIN(2 5 1k)
R1 in 0 1k
.end
""")
    src = _only_source(c)
    assert src.value == 3


def test_parse_sin_with_ac_keeps_offset():
    c = parse("""t
V1 in 0 AC 1 SIN(2 5 1k)
R1 in 0 1k
.end
""")
    src = _only_source(c)
    assert src.value == 2
    assert src.ac_value == 1


# ---------------------------------------------------------------------------
# PULSE
# ---------------------------------------------------------------------------

def test_parse_pulse_step():
    c = parse("""t
V1 in 0 PULSE(0 5)
R1 in 0 1k
.end
""")
    src = _only_source(c)
    assert src.waveform == "pulse"
    assert (src.v1, src.v2, src.td, src.pw) == (0, 5, 0, cas.oo)


def test_parse_pulse_full():
    c = parse("""t
V1 in 0 PULSE(0 5 1u 0 0 10u)
R1 in 0 1k
.end
""")
    src = _only_source(c)
    assert src.td == cas.Rational(1, 10**6)
    assert src.pw == cas.Rational(1, 10**5)


def test_parse_pulse_rejects_rise_fall():
    with pytest.raises(ValueError, match="rise/fall"):
        parse("""t
V1 in 0 PULSE(0 5 0 1n 1n 10u)
R1 in 0 1k
.end
""")


def test_parse_pulse_rejects_period():
    with pytest.raises(ValueError, match="single-shot"):
        parse("""t
V1 in 0 PULSE(0 5 0 0 0 10u 20u)
R1 in 0 1k
.end
""")


# ---------------------------------------------------------------------------
# EXP
# ---------------------------------------------------------------------------

def test_parse_exp_rise_only():
    c = parse("""t
I1 in 0 EXP(0 1m 0 5u)
R1 in 0 1k
.end
""")
    src = _only_source(c)
    assert src.waveform == "exp"
    assert (src.v1, src.td1) == (0, 0)
    assert src.tau1 == cas.Rational(1, 200000)
    assert src.td2 is None and src.tau2 is None


def test_parse_exp_with_fall():
    c = parse("""t
V1 in 0 EXP(0 5 0 1u 10u 2u)
R1 in 0 1k
.end
""")
    src = _only_source(c)
    assert src.td2 == cas.Rational(1, 10**5)
    assert src.tau2 == cas.Rational(1, 500000)


def test_parse_exp_rejects_incomplete_fall():
    with pytest.raises(ValueError, match="td2 and tau2"):
        parse("""t
V1 in 0 EXP(0 5 0 1u 10u)
R1 in 0 1k
.end
""")


# ---------------------------------------------------------------------------
# IC= on C / L
# ---------------------------------------------------------------------------

def test_parse_capacitor_ic():
    c = parse("""t
C1 out 0 1u IC=5
R1 out 0 1k
.end
""")
    cap = next(comp for comp in c.components if comp.name == "C1")
    assert cap.ic == 5


def test_parse_inductor_ic_symbolic():
    c = parse("""t
L1 out 0 1m IC=I0
R1 out 0 1k
.end
""")
    ind = next(comp for comp in c.components if comp.name == "L1")
    assert ind.ic == cas.Symbol("I0")


def test_parse_cl_without_ic_stays_none():
    c = parse("""t
C1 out 0 1u
L1 out 0 1m
.end
""")
    for comp_name in ("C1", "L1"):
        comp = next(c_ for c_ in c.components if c_.name == comp_name)
        assert comp.ic is None


# ---------------------------------------------------------------------------
# Writer round-trips
# ---------------------------------------------------------------------------

def _roundtrip(netlist: str):
    return parse(to_spice(parse(netlist)))


@pytest.mark.parametrize("line", [
    "V1 in 0 SIN(2 5 1k)",
    "V1 in 0 SIN(0 5 1k 0 0 90)",
    "V1 in 0 AC 1 SIN(2 5 1k)",
    "V1 in 0 PULSE(0 5)",
    "V1 in 0 PULSE(0 5 1u 0 0 10u)",
    "I1 in 0 EXP(0 1m 0 5u)",
    "V1 in 0 EXP(0 5 0 1u 10u 2u)",
    "C1 in 0 1u IC=5",
    "L1 in 0 1m IC=2",
])
def test_roundtrip_preserves_fields(line):
    netlist = f"t\n{line}\nR1 in 0 1k\n.end\n"
    orig = parse(netlist)
    again = _roundtrip(netlist)
    by_name_orig = {c.name: c for c in orig.components}
    by_name_new = {c.name: c for c in again.components}
    assert set(by_name_orig) == set(by_name_new)
    for name, comp in by_name_orig.items():
        new = by_name_new[name]
        for attr in ("value", "ac_value", "waveform", "amplitude",
                     "frequency", "phase", "v1", "v2", "td", "pw",
                     "td1", "tau1", "td2", "tau2", "ic"):
            a = getattr(comp, attr, None)
            b = getattr(new, attr, None)
            if a is None or b is None or isinstance(a, str) or a == b:
                assert a == b, f"{name}.{attr}: {a!r} != {b!r}"
            else:
                assert cas.simplify(a - b) == 0, f"{name}.{attr}: {a!r} != {b!r}"


# ---------------------------------------------------------------------------
# End-to-end: netlist → solve_transient
# ---------------------------------------------------------------------------

def test_netlist_rc_step_transient():
    c = parse("""rc step
V1 in 0 PULSE(0 Vstep)
R1 in out R
C1 out 0 C
.end
""")
    tran = solve_transient(c, outputs=["out"], simplify=True)
    R, C, Vstep = (cas.Symbol(n) for n in ("R", "C", "Vstep"))
    expected = Vstep * (1 - cas.exp(-tran.t / (R * C)))
    vout = tran.t_solution[cas.Symbol("V(out)")]
    assert cas.simplify(vout - expected) == 0


def test_netlist_ic_natural_response():
    c = parse("""rc natural
C1 out 0 C IC=V0
R1 out 0 R
.end
""")
    tran = solve_transient(c, outputs=["out"], simplify=True)
    R, C, V0 = (cas.Symbol(n) for n in ("R", "C", "V0"))
    expected = V0 * cas.exp(-tran.t / (R * C))
    vout = tran.t_solution[cas.Symbol("V(out)")]
    assert cas.simplify(vout - expected) == 0
