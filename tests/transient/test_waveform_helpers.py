"""``waveform_laplace`` / ``waveform_time`` helper consistency tests.

For each waveform the time-domain helper's Laplace transform must
match what the Laplace helper stamps into the MNA right-hand side.
"""
from sycan import cas as cas

from sycan import VoltageSource, waveform_laplace, waveform_time


def _laplace_of(expr, t, s):
    return cas.laplace_transform(expr, t, s, noconds=True)


def test_sine_helpers_agree():
    A, f = cas.symbols("A f", positive=True)
    t = cas.Symbol("t", positive=True)
    s = cas.Symbol("s", positive=True)
    src = VoltageSource("V1", "a", "0", 0,
                        waveform="sine", amplitude=A, frequency=f)
    direct = waveform_laplace(src, s)
    via_time = _laplace_of(waveform_time(src, t), t, s)
    assert cas.simplify(direct - via_time) == 0


def test_pulse_helpers_agree():
    v1, v2, td, pw = cas.symbols("v1 v2 td pw", positive=True)
    t = cas.Symbol("t", positive=True)
    s = cas.Symbol("s", positive=True)
    src = VoltageSource("V1", "a", "0", 0,
                        waveform="pulse", v1=v1, v2=v2, td=td, pw=pw)
    direct = waveform_laplace(src, s)
    via_time = _laplace_of(waveform_time(src, t), t, s)
    assert cas.simplify(direct - via_time) == 0


def test_exp_helpers_agree():
    v1, v2, tau1 = cas.symbols("v1 v2 tau1", positive=True)
    t = cas.Symbol("t", positive=True)
    s = cas.Symbol("s", positive=True)
    src = VoltageSource("V1", "a", "0", 0,
                        waveform="exp", v1=v1, v2=v2, td1=0, tau1=tau1)
    direct = waveform_laplace(src, s)
    via_time = _laplace_of(waveform_time(src, t), t, s)
    assert cas.simplify(direct - via_time) == 0


def test_dc_value_is_step():
    """A plain DC source maps to value/s (step at t = 0)."""
    V = cas.Symbol("V", positive=True)
    s = cas.Symbol("s")
    t = cas.Symbol("t", positive=True)
    src = VoltageSource("V1", "a", "0", V)
    assert waveform_laplace(src, s) == V / s
    assert waveform_time(src, t) == V
