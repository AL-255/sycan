"""Compare the two ``autodraw`` grid-routing algorithms.

Runs ``autodraw(netlist, router=...)`` ``N_ITERS`` times per algorithm,
per fixture, and reports:

* ``cold_ms``    — first call (post-warmup), captures any path-cache /
  glyph-load effects that survive the harness's warmup.
* ``warm_ms``    — median of the remaining iterations.
* ``expansions`` — total number of grid cells popped from the priority
  queue during the final routing pass (summed across every net of the
  last call). The final pass is what ``router=`` actually swaps; the SA
  cost-evaluation grid uses its own BFS/Dijkstra implementation
  regardless of this flag, so its work is bundled into wall time but
  not into ``expansions``.

The cell-expansion count is the cleanest apples-to-apples measure: A*
with an admissible heuristic always produces a path of the same cost
as Dijkstra, so the only thing to compare on the routing layer is *how
many cells each algorithm had to pop* before reaching the goal.

Usage::

    .venv/bin/python bench/bench_router.py            # human-readable table
    .venv/bin/python bench/bench_router.py --json     # machine-readable
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from typing import Optional

# Patch _RouteGrid before any autodraw call so we can read per-call
# expansion counts. Counter is reset by the harness around each timed
# call and read out at the end. ``sycan.autodraw`` (the public name)
# is bound to the *function*; the module that owns _RouteGrid lives
# under the same dotted path, so reach for it via ``importlib`` rather
# than the package-level alias.
import importlib

_ad = importlib.import_module("sycan.autodraw")

_ROUTER_TOTAL_EXPANSIONS = 0
_orig_dijkstra = _ad._RouteGrid._dijkstra
_orig_astar = _ad._RouteGrid._astar


def _wrap_dijkstra(self, src, dst_set):
    global _ROUTER_TOTAL_EXPANSIONS
    out = _orig_dijkstra(self, src, dst_set)
    _ROUTER_TOTAL_EXPANSIONS += self.last_expansions
    return out


def _wrap_astar(self, src, dst_set):
    global _ROUTER_TOTAL_EXPANSIONS
    out = _orig_astar(self, src, dst_set)
    _ROUTER_TOTAL_EXPANSIONS += self.last_expansions
    return out


_ad._RouteGrid._dijkstra = _wrap_dijkstra
_ad._RouteGrid._astar = _wrap_astar

autodraw = _ad.autodraw


# ---------------------------------------------------------------------------
# Fixtures — a sweep across circuit complexity. Each entry is a SPICE
# netlist string with ``.end``. They are taken from the autodraw test
# corpus so any regression here is covered by ``tests/drawing/`` too.
# ---------------------------------------------------------------------------
FIXTURES: list[tuple[str, str, dict]] = [
    (
        "voltage_divider",
        """voltage divider
V1 VDD 0 5
R1 VDD mid 1k
R2 mid 0 1k
.end
""",
        {"power_nets": ("VDD",)},
    ),
    (
        "cs_amp",
        """CS amplifier
Vdd VDD 0 1.8
Vin gate 0 0.7
RL VDD drain 10k
M1 drain gate 0 NMOS_L1 mu_n Cox W L V_TH
.end
""",
        {},
    ),
    (
        "current_mirror",
        """NMOS current mirror
V1 VDD 0 1.8
I1 VDD ref DC 100u
M1 ref ref 0 NMOS_L1 mu_n Cox W L V_TH
M2 out ref 0 NMOS_L1 mu_n Cox W L V_TH
RL VDD out 5k
.end
""",
        {},
    ),
    (
        "ce_bjt",
        """NPN common emitter
Vdd VDD 0 5
Vbb base 0 0.7
RC VDD col 4.7k
Q1 col base emi NPN 1e-15 100 1
RE emi 0 1k
.end
""",
        {},
    ),
    (
        "diff_pair",
        """nmos diff pair
Vdd VDD 0 1.8
Vinp inp 0 0.9
Vinn inn 0 0.9
R1 VDD outp 5k
R2 VDD outn 5k
M1 outp inp tail NMOS_L1 mu_n Cox W L V_TH
M2 outn inn tail NMOS_L1 mu_n Cox W L V_TH
ITAIL tail 0 DC 100u
.end
""",
        {},
    ),
    (
        "cascode",
        """nmos cascode
Vdd VDD 0 1.8
Vbias bias 0 1.0
Vin in 0 0.7
RL VDD drainh 5k
M2 drainh bias mid NMOS_L1 mu_n Cox W L V_TH
M1 mid in 0 NMOS_L1 mu_n Cox W L V_TH
.end
""",
        {},
    ),
    (
        "level_shifter",
        """cross-coupled level shifter
V0   VDD  0    1.8
VINP IN_P 0    0.9
VINN IN_N 0    0.9
MP0  OUT_N OUT_P VDD PMOS_L1 mu_p Cox W L V_TH
MP1  OUT_P OUT_N VDD PMOS_L1 mu_p Cox W L V_TH
MN0  OUT_N IN_P  0   NMOS_L1 mu_n Cox W L V_TH
MN1  OUT_P IN_N  0   NMOS_L1 mu_n Cox W L V_TH
.end
""",
        {},
    ),
    (
        "srpp_triode",
        """SRPP triode amp
Vb VDD 0 DC 250
Vin in 0 DC 0.5
RL out 0 100k
X1 n_mid in 0 TRIODE 1m 100
X2 VDD n_mid out TRIODE 1m 100
Rs out n_mid 5k
.end
""",
        {},
    ),
]


N_ITERS = 11   # 1 cold + 10 warm
WARMUP = 1


def _bench_one(netlist: str, kwargs: dict, router: str
               ) -> tuple[float, float, int]:
    """Run autodraw N_ITERS times and return (cold_ms, warm_median_ms, exps).

    Resets the global expansion counter immediately before each call;
    ``expansions`` is the count from the *last* (warm) call.
    """
    global _ROUTER_TOTAL_EXPANSIONS

    # Warmup (untimed): primes the glyph cache, sympy/cas import, etc.
    for _ in range(WARMUP):
        autodraw(netlist, router=router, **kwargs)

    times_ms: list[float] = []
    last_expansions = 0
    for _ in range(N_ITERS):
        _ROUTER_TOTAL_EXPANSIONS = 0
        t0 = time.perf_counter()
        autodraw(netlist, router=router, **kwargs)
        dt_ms = (time.perf_counter() - t0) * 1000
        times_ms.append(dt_ms)
        last_expansions = _ROUTER_TOTAL_EXPANSIONS

    cold_ms = times_ms[0]
    warm_ms = statistics.median(times_ms[1:])
    return cold_ms, warm_ms, last_expansions


def _run_all() -> list[dict]:
    rows: list[dict] = []
    for name, netlist, kwargs in FIXTURES:
        row: dict = {"fixture": name}
        for router in ("dijkstra", "astar"):
            cold, warm, exps = _bench_one(netlist, kwargs, router)
            row[router] = {
                "cold_ms": round(cold, 2),
                "warm_ms": round(warm, 2),
                "expansions": exps,
            }
        d = row["dijkstra"]
        a = row["astar"]
        row["warm_ratio"] = round(a["warm_ms"] / d["warm_ms"], 3) if d["warm_ms"] else None
        row["exp_ratio"] = round(a["expansions"] / d["expansions"], 3) if d["expansions"] else None
        rows.append(row)
    return rows


def _print_table(rows: list[dict]) -> None:
    print(f"{'fixture':<18}  {'D ms (warm)':>11}  {'A* ms (warm)':>12}  "
          f"{'ratio':>6}  {'D exp':>8}  {'A* exp':>8}  {'ratio':>6}")
    print("-" * 80)
    for r in rows:
        d, a = r["dijkstra"], r["astar"]
        print(f"{r['fixture']:<18}  "
              f"{d['warm_ms']:>11.2f}  {a['warm_ms']:>12.2f}  "
              f"{r['warm_ratio']!s:>6}  "
              f"{d['expansions']:>8d}  {a['expansions']:>8d}  "
              f"{r['exp_ratio']!s:>6}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true",
                        help="Emit machine-readable JSON instead of a table")
    args = parser.parse_args()

    rows = _run_all()
    if args.json:
        json.dump(rows, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        _print_table(rows)


if __name__ == "__main__":
    main()
