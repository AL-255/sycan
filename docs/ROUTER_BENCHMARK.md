# Autodraw router benchmark

`autodraw()` exposes a `router=` flag that picks between the historical
uniform-cost Dijkstra (`"dijkstra"`, default) and an A\* variant
(`"astar"`). Both share the same edge-cost function — `1 + 4·used +
clearance + 2·turn` — so the per-net routed cost is identical; the
only difference is *how many cells the search expands* before reaching
the goal, and consequently how much wall time the final routing pass
spends.

## Methodology

Driver: [`bench/bench_router.py`](../bench/bench_router.py). Eight
fixtures from `tests/drawing/test_autodraw.py` (so any regression
caught here is also caught by the existing test suite). For each
fixture the harness:

1. Calls `autodraw(netlist, router=...)` once as warmup (primes glyph
   cache, sympy import, etc.).
2. Repeats the call 11 times. The first is the **cold** measurement;
   the **warm** number is the median of the remaining 10.
3. Patches `_RouteGrid._dijkstra` / `_astar` to count cell pops;
   reports the total across every net of the *last* call.

The expansion counter is the cleanest apples-to-apples view: it isolates
exactly the work the `router=` flag swaps.

## Environment

- CPU: AMD Ryzen 9 9955HX
- OS: Ubuntu 24.04, Linux 6.17
- Python 3.13.9, sympy 1.14.0
- sycan @ HEAD of `main`

## Results

| Fixture          | Dijkstra ms | A\* ms | wall ratio | Dijkstra cells | A\* cells | cell ratio |
|------------------|------------:|-------:|-----------:|---------------:|----------:|-----------:|
| voltage_divider  |        8.84 |   8.66 |      0.98× |              4 |         4 |      1.00× |
| cs_amp           |       12.83 |  12.51 |      0.98× |            334 |        90 |      0.27× |
| current_mirror   |       16.07 |  14.98 |      0.93× |            884 |       246 |      0.28× |
| ce_bjt           |       17.71 |  17.43 |      0.98× |            207 |        60 |      0.29× |
| diff_pair        |       44.34 |  41.82 |      0.94× |          2 934 |       802 |      0.27× |
| cascode          |       22.74 |  22.69 |      1.00× |            733 |       170 |      0.23× |
| level_shifter    |       24.76 |  24.95 |      1.01× |            663 |       152 |      0.23× |
| srpp_triode      |       24.00 |  22.30 |      0.93× |          1 960 |       521 |      0.27× |

(`cs_amp` is the smallest non-trivial circuit; `diff_pair` is the
busiest, dominated by a many-pin tail net plus two long rail stubs.)

Reproduce with:

```bash
.venv/bin/python bench/bench_router.py            # human-readable table
.venv/bin/python bench/bench_router.py --json > bench/router_results.json
```

## Reading the numbers

**Cell expansions: A\* wins consistently (3–4× fewer cells).** With the
admissible Manhattan-bbox heuristic, A\* steers the search front toward
the target instead of expanding a roughly circular region around the
source. The ratio is remarkably uniform across fixtures because every
net is small enough that the heuristic never bottoms out — for the
typical few-pin net the search is dominated by the goal-bias term.

**`voltage_divider` is the degenerate case.** Two pins both touch a
rail row; A\* and Dijkstra agree on the same 4-cell stub and the
heuristic has nothing to prune.

**Wall time: A\* is 0–7% faster.** Routing is a small slice of total
`autodraw()` runtime — most of the budget goes to the SA placement
loop, which keeps its own BFS/Dijkstra cost-evaluation grid (not
swapped by `router=`) and to glyph rendering. The 25–40 ms per call
isn't going to shrink to single digits no matter how clever the final
router is. The `diff_pair` and `srpp_triode` cases (the two with the
largest cell counts) show the biggest wall-time payoff at ~6–7%.

**Why isn't A\* dramatically faster on wall time even when it expands
4× fewer cells?** Each expansion in this codebase is cheap (a few
arithmetic ops + heap push), so the absolute cell count has to be in
the thousands for the per-cell saving to dominate the constant
overhead of bookkeeping and the heuristic computation. The largest
fixture here (`diff_pair`) pops ~3k cells under Dijkstra; even at
~0.7 ms of routing work the routing layer just isn't the bottleneck.

## When to pick which

- **dijkstra** *(default)* — keep it for now. Output is bit-identical
  modulo equal-cost tie breaking, performance is within a few percent,
  and it's the algorithm every existing test was authored against.
- **astar** — pick it on circuits where you expect routing to dominate
  (high pin count, large canvas, repeated calls) or when you want the
  smaller search footprint for profiling / instrumentation. The
  speedup is real but modest at sycan's current circuit sizes; it
  would compound on circuits an order of magnitude larger, where the
  routing pass becomes a meaningful fraction of total time.

## What's *not* swapped

`router=` only governs the `_RouteGrid` instance used at the *final*
routing pass. The SA inner loop (`_route_total_wirelength` /
`_route_total_hpwl` in `src/sycan/autodraw.py`) keeps its own
cost-evaluation grid and uses pure BFS or Dijkstra-with-clearance
regardless of the `router=` flag. That code path is hot during
optimisation but the cost it computes feeds back into placement
selection, not into the rendered SVG, so swapping it out is a
separate decision (and would change SA convergence behaviour).
