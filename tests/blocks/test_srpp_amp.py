"""SRPP (Series-Regulated Push-Pull) vacuum-tube amplifier.

Topology::

              V_B (HT)
               |
              plate(T2)
               |
             [T2]  upper triode
               |
              cathode(T2) = V_out    (output node)
               |
              R_s (sense resistor)
               |
              plate(T1) = grid(T2)    (tied together: T2's grid at the
                                       bottom of the sense resistor)
               |
             [T1]  lower triode, amplifier
               |
              cathode(T1) = GND
               |

             grid(T1) = V_in

Both triodes use the Langmuir 3/2-power law; the AC small-signal
parameters ``g_m`` and ``g_p`` fall out by :func:`sympy.diff` of the
DC law at the operating point.

Output-impedance derivation (input shorted via auto-termination)::

    Z_out(R_s) = r_p (R_s + r_p) / (R_s (mu + 1) + 2 r_p)

    R_s -> 0       =>   Z_out -> r_p / 2      (T2's grid is tied to its
                                                cathode: upper tube is
                                                a passive r_p)
    R_s -> infty   =>   Z_out -> r_p / (mu+1) (T1 is choked off; T2
                                                reduces to a cathode
                                                follower — the matched
                                                load for push-pull
                                                distortion cancellation)

The optimal load for 2nd-harmonic cancellation equals this matched
source impedance, namely

    R_L_opt  =  Z_out  =  r_p / (mu + 1)       (in the large-R_s limit)

which is exactly the condition the test below verifies symbolically.
"""
import sympy as sp

from sycan import parse, solve_impedance

_SRPP = """\
SRPP amplifier
P_in in 0 input
P_out out 0 output
Vb hv 0 DC V_B
X1 n_mid in 0 TRIODE K mu V_g_op V_p_op
X2 hv n_mid out TRIODE K mu V_g_op V_p_op
Rs out n_mid R_s
.end
"""

_SRPP_LOADED = """\
SRPP amplifier with load
P_in in 0 input
P_out out 0 output
Vb hv 0 DC V_B
X1 n_mid in 0 TRIODE K mu V_g_op V_p_op
X2 hv n_mid out TRIODE K mu V_g_op V_p_op
Rs out n_mid R_s
RL out 0 R_L
.end
"""


def _r_p(K, mu, V_g_op, V_p_op):
    """Small-signal plate resistance at (V_g_op, V_p_op)."""
    g_p = sp.Rational(3, 2) * K * (mu * V_g_op + V_p_op) ** sp.Rational(1, 2)
    return 1 / g_p


# ---------------------------------------------------------------------------

def test_srpp_zout_closed_form():
    """The symbolic SRPP output impedance matches the textbook
    ``r_p (R_s + r_p) / (R_s (mu+1) + 2 r_p)`` closed form."""
    K, mu, V_g_op, V_p_op, R_s, V_B = sp.symbols(
        "K mu V_g_op V_p_op R_s V_B"
    )
    Z_out = solve_impedance(parse(_SRPP), "P_out", termination="auto")

    r_p = _r_p(K, mu, V_g_op, V_p_op)
    expected = r_p * (R_s + r_p) / (R_s * (mu + 1) + 2 * r_p)
    assert sp.simplify(sp.together(Z_out - expected)) == 0


def test_srpp_optimal_load_for_distortion_cancellation():
    """In the large-R_s limit, Z_out -> r_p / (mu + 1); that matched
    source impedance is the optimal load R_L for 2nd-harmonic
    cancellation in an SRPP."""
    K, mu, V_g_op, V_p_op, R_s, V_B = sp.symbols(
        "K mu V_g_op V_p_op R_s V_B"
    )
    Z_out = solve_impedance(parse(_SRPP), "P_out", termination="auto")

    Z_out_limit = sp.limit(Z_out, R_s, sp.oo)
    r_p = _r_p(K, mu, V_g_op, V_p_op)
    R_L_opt = r_p / (mu + 1)
    assert sp.simplify(Z_out_limit - R_L_opt) == 0
