"""Common-emitter BJT small-signal amplifier modelled with the
hybrid-pi network. Verifies the classical midband gain
``Av = -gm * (Ro || RL) * Rpi / (Rs + Rpi)``."""
from sycan import cas as cas

from sycan import parse, solve_dc

# Input source Vs drives the base through Rs; Rpi is the input
# resistance between base and emitter (ground). The VCCS G1 models the
# collector current gm*V(b) flowing from c into ground, so it is
# stamped with N+=c and N-=0. Output load is Ro || RL at the collector.
NETLIST = """BJT common-emitter
V1 src 0 Vs; down
Rs src b Rs; right
Rpi b 0_1 Rpi; down
G1 c 0_2 b 0_1 gm; down
W5 c c2; right
Ro c2 0_3 Ro; down
W6 c2 c3; right
RL c3 0_4 RL; down
W1 0 0_1; right
W2 0_1 0_2; right
W3 0_2 0_3; right
W4 0_3 0_4; right
.end
"""


def test_ce_midband_gain():
    sol = solve_dc(parse(NETLIST))
    Vs, Rs, Rpi, gm, Ro, RL = cas.symbols("Vs Rs Rpi gm Ro RL")
    gain = sol[cas.Symbol("V(c)")] / Vs
    expected = -gm * (Ro * RL / (Ro + RL)) * Rpi / (Rs + Rpi)
    assert cas.simplify(gain - expected) == 0


def test_ce_base_voltage():
    sol = solve_dc(parse(NETLIST))
    Vs, Rs, Rpi = cas.symbols("Vs Rs Rpi")
    # Simple input divider between Rs and Rpi.
    assert cas.simplify(sol[cas.Symbol("V(b)")] - Vs * Rpi / (Rs + Rpi)) == 0
