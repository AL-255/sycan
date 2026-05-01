"""Self-biased bandgap reference: VCVS opamp + PMOS current mirror.

The earlier ``test_bandgap_reference`` exercises the PTAT/CTAT *math*
with two ideal current sources; this companion test wraps the same
diode core in a closed feedback loop so the bias current is generated
**by the circuit itself** rather than supplied as a parameter.

Topology
--------
::

                 V_DD
                  |
        +---------+---------+
        |         |         |
       Mp1       Mp2       Mp3      <- matched PMOS sources, gates tied
        |         |         |
        a         b        ref
        |         |         |
        D1       R1         R3
        |         |         |
       (gnd)      c        out
                  |         |
                 D2        D3
                  |         |
                (gnd)     (gnd)

                +---+
        a ------|-  |
                | E |---- g (PMOS gates)
        b ------|+  |
                +---+

* Mp1, Mp2, Mp3 are matched Shichman-Hodges PMOS in saturation
  (lambda = 0). Their common gate ``g`` is driven by an ideal opamp
  modelled as a high-gain VCVS ``E1: V(g) = A * (V(b) - V(a))``.
* In the ``A -> oo`` limit the opamp pins ``V(a) = V(b)`` (virtual
  short) so the ``DeltaV_BE`` between D1 and D2 falls entirely across
  R1, fixing the PTAT bias::

      I * R1 = V_BE1 - V_BE2  ==  V_T * ln(N)   (saturation limit, N = IS_N/IS)

  hence ``I = V_T * ln(N) / R1``.
* The output branch (Mp3 -> R3 -> D3) re-uses that PTAT current to
  produce::

      V_REF = V(ref) = V_BE3 + I * R3
                     = V_BE3 + (R3 / R1) * V_T * ln(N)

  i.e. CTAT (``V_BE3``) plus a tunable PTAT term whose slope ``R3/R1``
  is the classical bandgap multiplier ``K``.

Verification strategy
---------------------
The combined transcendental (Shockley) + quadratic (Shichman-Hodges)
operating-point system has no elementary closed form, so rather than
ask :func:`solve_dc` to crack it we:

1. assemble the symbolic MNA residuals with :func:`build_residuals`,
2. substitute the closed-form op-point we *expect* in the ``A -> oo``
   limit (parameterised by the unknown PTAT current ``I``, with R1
   defined implicitly so the loop closes), and
3. check every residual collapses to zero after taking ``A -> oo``.

If the topology, polarities, or PMOS current matching were wrong, at
least one residual would fail to vanish.
"""
from sycan import cas as cas

from sycan import build_residuals, parse


NETLIST = """bandgap with opamp + PMOS mirror
VDD vdd 0 V_DD
* Matched PMOS current mirror; common gate g driven by the opamp.
Mp1 a g vdd PMOS_L1 mu Cox W L V_THP
Mp2 b g vdd PMOS_L1 mu Cox W L V_THP
Mp3 ref g vdd PMOS_L1 mu Cox W L V_THP
* Ideal high-gain opamp: V(g) = A * (V(b) - V(a)).
E1 g 0 b a A
* Two PTAT-generating diode branches; D2 has N times the area (IS_N = N*IS).
D1 a 0 IS 1 V_T
R1 b c R1
D2 c 0 IS_N 1 V_T
* Output branch: V_REF = V_BE3 + I * R3.
R3 ref out R3
D3 out 0 IS 1 V_T
.end
"""


def _expected_operating_point():
    """Closed-form op-point in the A -> oo limit, parameterised by I.

    The implicit loop equation ``I * R1 = V_BE1(I) - V_BE2(I)`` is
    captured by treating R1 as the *derived* parameter
    ``(V_BE1 - V_BE2) / I``. The remaining node voltages then follow
    directly from KVL around each branch and from the PMOS saturation
    equation that pins ``V(g)`` for the chosen ``I``.

    A small ``V(b) = V(a) + V(g)/A`` offset is carried so the opamp
    aux equation ``V(g) - A*(V(b) - V(a)) = 0`` collapses analytically;
    the offset vanishes in the ``A -> oo`` limit and recovers the
    virtual short.
    """
    V_DD = cas.Symbol("V_DD")
    V_THP = cas.Symbol("V_THP")
    mu = cas.Symbol("mu")
    Cox = cas.Symbol("Cox")
    W = cas.Symbol("W")
    L = cas.Symbol("L")
    IS = cas.Symbol("IS")
    IS_N = cas.Symbol("IS_N")
    V_T = cas.Symbol("V_T")
    R3 = cas.Symbol("R3")
    A = cas.Symbol("A")
    I = cas.Symbol("I_bias")

    V_BE1 = V_T * cas.log(I / IS + 1)
    V_BE2 = V_T * cas.log(I / IS_N + 1)
    V_BE3 = V_BE1                                  # D3 matches D1

    R1_value = (V_BE1 - V_BE2) / I                 # bandgap-loop closure

    # PMOS in saturation with lambda = 0:
    #     I = (1/2) * mu * Cox * (W/L) * (V_DD - V(g) - V_THP)**2
    # Solving for V(g) (positive overdrive sqrt branch):
    beta = mu * Cox * W / L
    V_g = V_DD - V_THP - cas.sqrt(2 * I / beta)

    op = {
        cas.Symbol("V(vdd)"): V_DD,
        cas.Symbol("V(a)"): V_BE1,
        cas.Symbol("V(b)"): V_BE1 + V_g / A,
        cas.Symbol("V(c)"): V_BE2,
        cas.Symbol("V(g)"): V_g,
        cas.Symbol("V(ref)"): V_BE3 + I * R3,
        cas.Symbol("V(out)"): V_BE3,
        cas.Symbol("I(VDD)"): -3 * I,               # VDD sources 3*I (SPICE sign)
        cas.Symbol("I(E1)"): 0,                     # PMOS gates draw no DC current
    }
    extras = {cas.Symbol("R1"): R1_value}
    return op, extras, A, I


def test_bandgap_residuals_vanish():
    """Every MNA residual reduces to 0 at the expected op-point.

    A single substitution exercises:
    * PMOS saturation matching across all three legs (matched currents).
    * The VCVS opamp pinning V(a) = V(b) via negative feedback.
    * Diode KCL at each branch tail (D1, D2, D3).
    * R1 dropping DeltaV_BE (the PTAT mechanism).
    * R3 + D3 producing V_REF = V_BE3 + I * R3.
    """
    _, residuals = build_residuals(parse(NETLIST), mode="dc")
    op, extras, A, _ = _expected_operating_point()
    sub = {**op, **extras}

    for r in residuals:
        r_at_op = cas.simplify(r.subs(sub))
        r_lim = cas.limit(r_at_op, A, cas.oo)
        assert cas.simplify(r_lim) == 0, f"residual did not vanish: {r}"


def test_bandgap_ptat_loop_in_saturation_limit():
    """In the deep-saturation limit ``IS << I`` the loop reduces to the
    textbook PTAT relation ``I * R1 = V_T * ln(N)``."""
    IS, V_T, R1, N = cas.symbols("IS V_T R1 N", positive=True)
    I = cas.Symbol("I_bias", positive=True)
    IS_N = N * IS

    V_BE1 = V_T * cas.log(I / IS + 1)
    V_BE2 = V_T * cas.log(I / IS_N + 1)
    loop = cas.expand_log(V_BE1 - V_BE2, force=True)

    # Drop the "+1" in each log (forward-active / saturation regime).
    sat_loop = loop.rewrite(cas.log).subs(
        {cas.log(I / IS + 1): cas.log(I / IS),
         cas.log(I / IS_N + 1): cas.log(I / IS_N)}
    )
    assert cas.simplify(sat_loop - V_T * cas.log(N)) == 0


def test_bandgap_output_ptat_slope():
    """V_REF = V_BE3 + I * R3; with the loop fixing I = DeltaV_BE / R1,
    the PTAT contribution to V_REF carries the classical K = R3/R1
    multiplier on the (independent) DeltaV_BE drop."""
    R1, R3 = cas.symbols("R1 R3", positive=True)
    delta_VBE = cas.Symbol("DeltaVBE", positive=True)
    V_BE3 = cas.Symbol("V_BE3", positive=True)        # treated as independent CTAT

    I = delta_VBE / R1
    V_REF = V_BE3 + I * R3
    assert cas.simplify(cas.diff(V_REF, delta_VBE) - R3 / R1) == 0
    assert cas.simplify(V_REF.subs(delta_VBE, 0) - V_BE3) == 0
