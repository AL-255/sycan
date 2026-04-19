"""2-transistor sub-threshold V_T reference

Topology::

    VDD ●
        │
       M1  D=vdd, G=0 (VSS), S=n1
        │
        ● n1
        │
       M2  D=n1, G=n1, S=0   (diode-connected)
        │
        ⏚ GND

Each device carries its own geometry, threshold, AND sub-threshold
slope factor: ``mu_n_i``, ``Cox_i``, ``W_i``, ``L_i``, ``V_TH_i``,
``m_i`` (subscript i ∈ {1, 2}). The thermal voltage ``V_T`` is a
shared process parameter.

Using the sub-threshold drain-current model

    I_D = mu_n * Cox * (W/L) * V_T**2
          * exp((V_GS - m V_TH) / (m V_T))
          * (1 - exp(-V_DS / V_T))

define ``K_i = mu_n_i * Cox_i * (W_i/L_i) * V_T**2``. In the VDD >> V_T
limit M1 is in saturation, and if ``V(n1) >> V_T`` then M2 is too, so
I_D1 = I_D2 reduces to

    K1 * exp((-V(n1) - m1 V_TH1) / (m1 V_T))
        = K2 * exp(( V(n1) - m2 V_TH2) / (m2 V_T))

Taking logs and collecting ``V(n1)`` gives the classical closed form::

    V(n1) = m1 * m2 / (m1 + m2) * (V_TH2 - V_TH1)
          + m1 * m2 / (m1 + m2) * V_T
            * ln(mu_n1 * Cox1 * W1 * L2 / (mu_n2 * Cox2 * W2 * L1))

The (V_TH2 - V_TH1) term and the log term each carry the same
``m1*m2/(m1+m2)`` coefficient because ``V_TH`` enters the exponent at
``V_T`` scale (``-V_TH/V_T``) while ``V_GS`` enters at ``m V_T`` scale.


Reference:

- [1] M. Seok, G. Kim, D. Blaauw and D. Sylvester, "A Portable 2-Transistor Picowatt Temperature-Compensated Voltage Reference Operating at 0.5 V," in IEEE Journal of Solid-State Circuits, vol. 47, no. 10, pp. 2534-2545, Oct. 2012, doi: 10.1109/JSSC.2012.2206683.
"""
import sympy as sp

from sycan import parse

NETLIST = """2T reference with asymmetric devices
V1 vdd 0 VDD
M1 vdd 0 n1 NMOS_subthreshold mu_n1 Cox1 W1 L1 V_TH1 m1 V_T
M2 n1 n1 0 NMOS_subthreshold mu_n2 Cox2 W2 L2 V_TH2 m2 V_T
.end
"""


def test_2t_reference_asymmetric_equilibrium():
    (
        mu_n1, mu_n2, Cox1, Cox2, W1, W2, L1, L2,
        V_TH1, V_TH2, m1, m2, V_T, Vn1,
    ) = sp.symbols(
        "mu_n1 mu_n2 Cox1 Cox2 W1 W2 L1 L2 V_TH1 V_TH2 m1 m2 V_T Vn1",
        positive=True,
    )

    # Saturated M1 and M2 (VDD >> V_T and V(n1) >> V_T). KCL I_D1 = I_D2
    # becomes K1 * exp(arg1) = K2 * exp(arg2); equivalently,
    #     log(K1/K2) + arg1 - arg2 = 0.
    # Working in log space sidesteps sympy's exp simplification limits.
    arg1 = (-Vn1 - m1 * V_TH1) / (m1 * V_T)
    arg2 = (Vn1 - m2 * V_TH2) / (m2 * V_T)
    log_K1_over_K2 = sp.log(
        mu_n1 * Cox1 * W1 * L2 / (mu_n2 * Cox2 * W2 * L1)
    )

    expected = (
        m1 * m2 / (m1 + m2) * (V_TH2 - V_TH1)
        + m1 * m2 / (m1 + m2) * V_T * log_K1_over_K2
    )

    residual = (log_K1_over_K2 + arg1 - arg2).subs(Vn1, expected)
    assert sp.simplify(residual) == 0


def test_2t_reference_collapses_when_symmetric():
    """If the two devices share every parameter, V(n1) = 0."""
    (
        mu_n1, mu_n2, Cox1, Cox2, W1, W2, L1, L2,
        V_TH1, V_TH2, m1, m2, V_T,
    ) = sp.symbols(
        "mu_n1 mu_n2 Cox1 Cox2 W1 W2 L1 L2 V_TH1 V_TH2 m1 m2 V_T",
        positive=True,
    )
    expected = (
        m1 * m2 / (m1 + m2) * (V_TH2 - V_TH1)
        + m1 * m2 / (m1 + m2) * V_T * sp.log(
            mu_n1 * Cox1 * W1 * L2 / (mu_n2 * Cox2 * W2 * L1)
        )
    )
    symmetric = expected.subs(
        {mu_n2: mu_n1, Cox2: Cox1, W2: W1, L2: L1, V_TH2: V_TH1, m2: m1}
    )
    assert sp.simplify(symmetric) == 0


def test_2t_reference_parses_without_error():
    from sycan.components.active import NMOS_subthreshold

    circuit = parse(NETLIST)
    mosfets = [c for c in circuit.components if isinstance(c, NMOS_subthreshold)]
    assert len(mosfets) == 2
    assert {m.name for m in mosfets} == {"M1", "M2"}
    m1, m2 = (next(m for m in mosfets if m.name == n) for n in ("M1", "M2"))
    # Device-level parameters should be distinct symbols.
    assert m1.W != m2.W
    assert m1.V_TH != m2.V_TH
    assert m1.m != m2.m
