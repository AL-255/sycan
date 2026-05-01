"""Series / parallel resistor noise — superposition of two thermal sources."""
from sycan import cas as cas

from sycan import Circuit, T_kelvin, k_B, solve_noise
from sycan.components.basic import Capacitor, Resistor, VoltageSource


def test_series_resistors_at_midpoint():
    """``R1`` from ``in→mid``, ``R2`` from ``mid→0``. The ideal V-source
    at ``in`` is small-signal-zero, so the impedance seen by either
    resistor's noise current at ``mid`` is ``R1 || R2`` and superposition
    gives ``S_V_mid = 4·k_B·T·(R1||R2)``."""
    R1, R2 = cas.symbols("R1 R2", positive=True)
    c = Circuit()
    c.add(VoltageSource("V1", "in", "0", value=0, ac_value=0))
    c.add(Resistor("R1", "in", "mid", R1, include_noise="thermal"))
    c.add(Resistor("R2", "mid", "0", R2, include_noise="thermal"))

    total, contribs = solve_noise(c, "mid", simplify=True)
    expected = 4 * k_B * T_kelvin * R1 * R2 / (R1 + R2)

    assert cas.simplify(total - expected) == 0
    assert set(contribs) == {"R1.thermal", "R2.thermal"}


def test_only_one_resistor_noisy_in_pair():
    """With only R1 noisy and R2 silent, the result reduces to a
    voltage-divider transfer of the R1 noise."""
    R1, R2 = cas.symbols("R1 R2", positive=True)
    c = Circuit()
    c.add(VoltageSource("V1", "in", "0", value=0, ac_value=0))
    c.add(Resistor("R1", "in", "mid", R1, include_noise="thermal"))
    c.add(Resistor("R2", "mid", "0", R2))  # silent

    total, _ = solve_noise(c, "mid", simplify=True)
    # |H_R1|² · S_R1 = (R1||R2)² · 4kT/R1 = 4kT · R1 · R2² / (R1+R2)²
    expected = 4 * k_B * T_kelvin * R1 * R2 ** 2 / (R1 + R2) ** 2
    assert cas.simplify(total - expected) == 0


def test_rc_lowpass_psd_at_dc_and_pole():
    """RC low-pass: thermal noise from R produces a single-pole roll-off.

    At ``s = j·ω`` the symbolic PSD becomes ``4kT·R / (1 + (ωRC)²)``.
    Verify the DC value (``ω = 0``) is ``4kT·R`` and the −3 dB point
    (``ω = 1/(R·C)``) is ``2kT·R``.
    """
    R, C, omega = cas.symbols("R C omega", positive=True)
    c = Circuit()
    c.add(VoltageSource("V1", "in", "0", value=0, ac_value=0))
    c.add(Resistor("R1", "in", "out", R, include_noise="thermal"))
    c.add(Capacitor("C1", "out", "0", C))

    s = cas.Symbol("s")
    total, _ = solve_noise(c, "out", s=s, simplify=True)
    psd_omega = cas.simplify(total.subs(s, cas.I * omega))
    expected = 4 * k_B * T_kelvin * R / (1 + (omega * R * C) ** 2)
    assert cas.simplify(psd_omega - expected) == 0

    # DC asymptote
    assert cas.simplify(psd_omega.subs(omega, 0) - 4 * k_B * T_kelvin * R) == 0
    # −3 dB point: ω = 1/(R C) ⇒ PSD halved
    assert (
        cas.simplify(psd_omega.subs(omega, 1 / (R * C)) - 2 * k_B * T_kelvin * R)
        == 0
    )
