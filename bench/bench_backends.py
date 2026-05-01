"""End-to-end backend speed comparison harness.

Runs a fixed set of sycan workflows ``N_ITERS`` times after a single
warmup, prints the cold-call (first run) and warm (median over the
remaining iterations) timings as a JSON list. Intended to be invoked
once per backend with ``SYCAN_CAS_BACKEND`` set, then the two outputs
diffed in the report.

Usage::

    SYCAN_CAS_BACKEND=sympy     .venv/bin/python bench/bench_backends.py
    SYCAN_CAS_BACKEND=symengine .venv/bin/python bench/bench_backends.py
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import time
from typing import Callable

# Backend selection happens here, before any sycan import.
_BACKEND = os.environ.get("SYCAN_CAS_BACKEND", "sympy")
os.environ["SYCAN_CAS_BACKEND"] = _BACKEND

import numpy as np  # noqa: E402

from sycan import (  # noqa: E402
    Circuit,
    NMOS_L1,
    Resistor,
    VoltageSource,
    parse,
    parse_value,
    solve_ac,
    solve_dc,
    solve_headroom,
    solve_impedance,
    solve_noise,
)
from sycan import cas as sp  # noqa: E402
from sycan.mna import build_mna  # noqa: E402
from sycan.polynomials import butterworth  # noqa: E402

assert sp.backend_name() == _BACKEND, (
    f"backend mismatch: requested {_BACKEND!r}, got {sp.backend_name()!r}"
)

N_ITERS = 100


# ---------------------------------------------------------------------------
# Scenarios. Each entry: (name, setup() -> ctx, body(ctx) -> any)
# ---------------------------------------------------------------------------
def _setup_voltage_divider():
    return """voltage divider
V1 in 0 Vin
R1 in out R1
R2 out 0 R2
.end
"""


def _setup_rc_lowpass():
    return """RC LP
V1 in 0 AC Vin
R1 in out R
C1 out 0 C
.end
"""


def _setup_rlc_two_port():
    # RC ladder with a port at each end — exercises ``solve_impedance``
    # in both auto-termination directions.
    return """RLC ladder
R1 in mid R1
L1 mid out L1
C1 out 0  C1
P_in  in  0 input
P_out out 0 output
.end
"""


def _setup_cs_polynomial():
    # CS amp with a numeric V_in input — residuals form a polynomial
    # system that ``sp.solve`` closes quickly on either backend.
    # Diode I-V (transcendental, Lambert-W) and BJT G-P (high degree)
    # both blow up sympy.solve, so we keep this benchmark on the
    # quick-closing polynomial path.
    c = Circuit()
    V_DD, V_TH, beta, R_L = sp.symbols("V_DD V_TH beta R_L", positive=True)
    c.add_vsource("Vdd", "VDD", "0", V_DD)
    c.add_vsource("Vin", "in",  "0", sp.Rational(1, 1))  # numeric drive
    c.add_resistor("RL", "VDD", "out", R_L)
    c.add_nmos_l1(
        "MN", "out", "in", "0",
        mu_n=beta, Cox=1, W=1, L=1, V_TH=V_TH, lam=0,
    )
    return c


def _setup_2t_vref():
    # Two transistors stacked — a slightly larger nonlinear DC system
    # that both backends can close.
    return """2t voltage reference
V1 VDD 0 VDD
M1 mid VDD VDD NMOS_L1 mu_n Cox W L V_TH
M2 mid mid 0   NMOS_L1 mu_n Cox W L V_TH
.end
"""


def _setup_butterworth_input():
    # Polynomial-prototype workload.
    return None


# Bench bodies — each takes the precomputed setup context and exercises
# one analysis path. Returning a non-trivial value keeps the result alive
# so the optimiser does not elide work.
def _bench_parse_value(_ctx):
    out = []
    for tok in ("1k", "2.5meg", "100u", "47p", "3.3", "1e-12"):
        out.append(parse_value(tok))
    return out


def _bench_parse(ctx):
    return parse(ctx)


def _bench_dc_linear_small(ctx):
    return solve_dc(parse(ctx))


def _bench_dc_linear_medium(ctx):
    # The 2T vref network is nonlinear; build_mna gives a comparable
    # linear workload across backends without simplification cost.
    return build_mna(parse(ctx), mode="dc")


def _bench_ac(ctx):
    sol = solve_ac(parse(ctx))
    return sol[sp.Symbol("V(out)")]


def _bench_dc_nonlinear(ctx):
    return solve_dc(ctx, simplify=False)


def _bench_impedance(ctx):
    c = parse(ctx)
    z_in = solve_impedance(c, "P_in")
    z_out = solve_impedance(c, "P_out")
    return z_in, z_out


def _bench_noise(_ctx):
    from sycan.components.basic.capacitor import Capacitor as _Capacitor
    from sycan.components.basic.voltage_source import VoltageSource as _VS
    c = Circuit()
    R, C = sp.symbols("R C", positive=True)
    c.add(_VS("V1", "in", "0", 0))
    c.add(Resistor("R1", "in", "out", R, include_noise="thermal"))
    c.add(_Capacitor("C1", "out", "0", C))
    total, _per = solve_noise(c, "out")
    return total


def _bench_headroom():
    V_DD, V_TH, beta, R_L = sp.symbols("V_DD V_TH beta R_L", positive=True)
    V_in = sp.Symbol("V_in", real=True)
    c = Circuit()
    c.add_vsource("Vdd", "VDD", "0", V_DD)
    c.add_vsource("Vin", "in",  "0", V_in)
    c.add_resistor("RL", "VDD", "out", R_L)
    c.add_nmos_l1(
        "MN", "out", "in", "0",
        mu_n=beta, Cox=1, W=1, L=1, V_TH=V_TH, lam=0,
    )
    op = {
        sp.Symbol("V(VDD)"): V_DD,
        sp.Symbol("V(in)"):  V_in,
        sp.Symbol("V(out)"): V_DD - sp.Rational(1, 2) * R_L * beta * (V_in - V_TH) ** 2,
    }
    return c, V_in, op


def _bench_headroom_call(ctx):
    c, V_in, op = ctx
    return solve_headroom(c, "Vin", var=V_in, op_point=op, simplify=False)


def _bench_butterworth(_ctx):
    return butterworth(5)


def _bench_lambdify(_ctx):
    x, y = sp.symbols("x y")
    expr = sp.exp(-(x ** 2 + y ** 2)) * (x * y + sp.sin(x))
    f = sp.lambdify([[x, y]], expr, modules="numpy")
    grid = np.linspace(-1.0, 1.0, 32)
    xs, ys = np.meshgrid(grid, grid)
    return float(np.sum(f([xs, ys])))


SCENARIOS: list[tuple[str, Callable, Callable]] = [
    ("parse_value (6 tokens)",        lambda: None,                       _bench_parse_value),
    ("parse small netlist",           _setup_voltage_divider,             _bench_parse),
    ("solve_dc linear (vdivider)",    _setup_voltage_divider,             _bench_dc_linear_small),
    ("build_mna DC (2T-vref)",        _setup_2t_vref,                     _bench_dc_linear_medium),
    ("solve_ac H(s) RC lowpass",      _setup_rc_lowpass,                  _bench_ac),
    ("solve_dc nonlinear (CS amp)",   _setup_cs_polynomial,               _bench_dc_nonlinear),
    ("solve_impedance Z_in / Z_out",  _setup_rlc_two_port,                _bench_impedance),
    ("solve_noise RC thermal",        lambda: None,                       _bench_noise),
    ("solve_headroom (op_point)",     _bench_headroom,                    _bench_headroom_call),
    ("butterworth(5) prototype",      lambda: None,                       _bench_butterworth),
    ("lambdify+evaluate 32x32 grid",  lambda: None,                       _bench_lambdify),
]


def time_scenario(name: str, setup: Callable, body: Callable) -> dict:
    ctx = setup()
    timings = []
    for _ in range(N_ITERS):
        t0 = time.perf_counter()
        body(ctx)
        timings.append(time.perf_counter() - t0)
    cold = timings[0]
    warm = statistics.median(timings[1:])
    return {
        "name": name,
        "cold_s": cold,
        "warm_median_s": warm,
        "warm_min_s": min(timings[1:]),
        "warm_max_s": max(timings[1:]),
    }


def main() -> None:
    results = [time_scenario(*s) for s in SCENARIOS]
    output = {
        "backend": _BACKEND,
        "python": sys.version.split()[0],
        "n_iters": N_ITERS,
        "scenarios": results,
    }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
