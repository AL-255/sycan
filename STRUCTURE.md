# SYCAN — Repository Structure

**SYCAN** (Symbolic Circuit ANalysis) is a Python 3.11+ package for symbolic circuit simulation built on SymPy, with support for DC, AC, and noise analysis, automatic schematic drawing, and an in-browser REPL.

```
sycan/
├── src/sycan/              # Main Python package
│   ├── __init__.py         # Package init, entry point main()
│   ├── mna.py              # Modified Nodal Analysis engine
│   ├── circuit.py          # Circuit representation / builder
│   ├── headroom.py         # Headroom analysis
│   ├── polynomials.py      # Polynomial utilities
│   ├── network_params.py   # Network parameter calculations
│   ├── spice.py            # SPICE-related functionality
│   ├── schematic.py        # Schematic handling
│   ├── autodraw.py         # Automatic schematic drawing
│   ├── autodraw_hacks.py   # Drawing hacks / utilities
│   ├── plot_util.py        # Plotting utilities
│   ├── svg_util.py         # SVG utility functions
│   ├── cas/                # CAS backend abstraction
│   │   ├── __init__.py
│   │   ├── _sympy_backend.py       # SymPy backend (default)
│   │   └── _symengine_backend.py   # SymEngine backend (optional)
│   └── components/         # Circuit component models
│       ├── __init__.py
│       ├── basic/          # Two-terminal components
│       │   ├── resistor.py, capacitor.py, inductor.py
│       │   ├── voltage_source.py, current_source.py
│       │   ├── vcvs.py, vccs.py, ccvs.py, cccs.py  # Controlled sources
│       │   ├── gnd.py      # Ground node
│       │   └── port.py     # Named port marker
│       ├── active/         # Active semiconductor devices
│       │   ├── bjt.py      # NPN/PNP BJT
│       │   ├── diode.py    # Shockley diode
│       │   ├── mosfet_l1.py, mosfet_3t.py, mosfet_4t.py
│       │   ├── mosfet_subthreshold.py
│       │   └── triode.py   # Vacuum triode
│       ├── blocks/         # Higher-level circuit blocks
│       │   ├── opamp.py, gain.py, integrator.py
│       │   ├── summer.py, quantizer.py
│       │   ├── subcircuit.py, transfer_function.py
│       │   └── ...
│       └── rf/             # RF components
│           └── tline.py    # Transmission line
│
├── tests/                  # Test suite (pytest)
│   ├── conftest.py
│   ├── DC/                 # DC analysis tests (23 files)
│   ├── AC/                 # AC analysis tests (6 files)
│   ├── noise/              # Noise analysis tests (4 files)
│   ├── blocks/             # Circuit block tests (8 files)
│   ├── rf/                 # RF component tests (1 file)
│   └── drawing/            # autodraw tests (2 files)
│
├── bench/                  # Performance benchmarks
│   ├── bench_backends.py   # SymPy vs SymEngine comparison
│   ├── bench_router.py
│   ├── results.json, router_results.json
│   └── run.sh
│
├── docs/                   # Documentation & web assets
│   ├── README.md           # Docs landing page
│   ├── analysis.md, BE_BENCHMARK.md, BE_PORT_STATUS.md
│   ├── repl/               # In-browser REPL (Pyodide)
│   │   ├── index.html
│   │   ├── sycan-*.whl     # Prebuilt wheel
│   │   └── examples/       # Example scripts
│   └── sedra/              # Browser schematic editor (TypeScript)
│       ├── index.html
│       ├── src/            # TypeScript sources
│       │   ├── editor.ts, glyphs.ts
│       ├── tests/          # Puppeteer browser tests
│       ├── package.json, tsconfig.json
│       └── node_modules/
│
├── res/                    # Component glyph library (SVGs)
│   ├── *.svg               # Flattened SVGs for autodraw
│   ├── inkscape/           # Source Inkscape SVGs
│   ├── sycan.png           # Project logo
│   └── svg_to_plain.sh     # SVG flattening script
│
├── sphinx/                 # Sphinx documentation source
│   ├── conf.py, index.rst
│   ├── api.rst, autodraw.rst, examples.rst
│   ├── getting_started.rst, glyphs.rst
│   ├── _static/            # Static assets (CSS, logo)
│   └── _build/             # Build output
│
├── pyproject.toml          # Project metadata & build config
├── uv.lock                 # Lock file (uv resolver)
├── LICENSE                 # GPL v2
├── README.md               # Project README
├── run_tests.sh            # Run pytest suite
├── run_webpage.sh          # Build & serve docs locally
├── .github/                # GitHub Actions CI/CD
├── .python-version         # Python version pin
├── .gitignore
└── _site/                  # GitHub Pages deploy output
```

## Key entry points

| Entry | Purpose |
|---|---|
| `src/sycan/__init__.py` → `main()` | CLI entry point (`sycan` command) |
| `src/sycan/circuit.py` | Primary API for building circuits |
| `src/sycan/mna.py` | Core symbolic simulation engine |
| `src/sycan/autodraw.py` | Automatic schematic rendering |
| `run_tests.sh` | Run full test suite |
| `run_webpage.sh` | Build + serve docs locally on port 8000 |

## Analysis Modes

- **DC** — Operating point (MNA with Newton-Raphson for nonlinear devices)
- **AC** — Small-signal frequency response
- **Noise** — Device noise contribution analysis

## Dependencies

- **Runtime**: Python ≥3.11, SymPy ≥1.12
- **Dev**: pytest, lcapy
- **Docs**: Sphinx, Furo theme, MyST parser
