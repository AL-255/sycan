"""Shot noise of a diode under reverse-biased load resistor."""
from sycan import cas as cas

from sycan import Circuit, q, solve_noise
from sycan.components.active import Diode
from sycan.components.basic import Resistor, VoltageSource


def test_diode_shot_noise_into_load():
    """Diode anode at 0, cathode driven through ``R_L`` from a quiet bias.

    The shot-noise current ``i_n`` flows between anode (gnd) and cathode.
    The diode's small-signal conductance ``g_d`` appears in parallel with
    the noise source, so ``V_k = i_n · (R_L || 1/g_d)``.
    """
    R_L, I_op, IS = cas.symbols("R_L I_op IS", positive=True)
    c = Circuit()
    c.add(VoltageSource("V1", "vdd", "0", value=0, ac_value=0))
    c.add(Resistor("RL", "vdd", "k", R_L))
    c.add(Diode("D1", "0", "k", IS, I_op=I_op, include_noise="shot"))

    total, contribs = solve_noise(c, "k", simplify=True)
    # g_d = IS * exp(V_D_op/(N*V_T)) / (N*V_T), N=1, V_T=517/20000
    # V_k = i_n * R_L / (1 + g_d * R_L)
    V_D_op = cas.Symbol("V_D_op_D1")
    V_T = cas.Rational(517, 20000)
    g_d = IS * cas.exp(V_D_op / V_T) / V_T
    expected = 2 * q * I_op * R_L ** 2 / (1 + g_d * R_L) ** 2

    assert cas.simplify(total - expected) == 0
    assert set(contribs) == {"D1.shot"}


def test_diode_default_I_op_is_symbolic():
    """No ``I_op`` argument → per-instance symbol ``I_op_<name>``."""
    d = Diode("D7", "a", "k", cas.Symbol("IS", positive=True),
              include_noise="shot")
    assert d.I_op == cas.Symbol("I_op_D7")
