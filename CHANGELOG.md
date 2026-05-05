# Changelog

All notable changes to SYCAN are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `main()` CLI entry point with `--version` and `parse` subcommand.
- PyPI classifiers, license metadata, and `keywords` in `pyproject.toml`.
- Optional `[symengine]` extra for the faster CAS backend.
- `sycan.check`, `sycan.headroom`, `sycan.plot_util`, `sycan.cas`, and
  `sycan.components.blocks` in the Sphinx API reference.
- Python 3.11 / 3.12 / 3.13 matrix in CI.

### Changed
- Expanded `README.md` with installation, quick-start example, and an
  analysis-mode reference table.

### Removed
- Pre-built REPL wheel (`docs/repl/sycan-*.whl`) — built fresh by CI and
  `run_webpage.sh`, no longer committed to the repo.

## [0.1.7] — 2026

### Added
- DC sweep (`solve_dc_sweep`).
- Transfer-function solver (`solve_tf`).
- Sensitivity analysis (`solve_sensitivity`).
- Behavioral voltage and current sources.
- Varactor and voltage-controlled switch components.
- Design rule check / ERC (`check_circuit`).
- JFET model (NJFET / PJFET).
- Pole–zero analysis (`solve_pz`).
- Mutual inductance (K) coupling.

### Changed
- Sedra schematic editor: mobile device improvements, glyph updates.

## [0.1.x] — earlier

### Added
- AC small-signal models for diode and BJT (hybrid-π).
- Flicker (1/f) noise for MOSFET L1/4T/subthreshold and BJT.
- Lossy transmission line.
- First-order op-amp (`OPAMP1`) with finite GBW and output impedance.
- PULSE and EXP waveform sources (Laplace-domain stamps).
- A* autorouter for `autodraw`.
- Sedra in-browser schematic editor (TypeScript port).
- SubCircuit support.
- SymEngine backend (multi-backend CAS abstraction).
- Linear-system modelling blocks (Gain, Integrator, Summer, Quantizer,
  TransferFunction). Type-II PLL and ΣΔ modulator REPL examples.
- Headroom analysis (`solve_headroom`).
- Glyph reference page in Sphinx docs.
- MOSFET 4T model and NAND2 stacking-effect demo.
- MOSFET 3T model bridging L1 and subthreshold.
- Engineering-format helpers in `plot_util`.
- Schematic back-annotation.
- Retry / double-pass simulated annealing for `autodraw` to reduce wire
  overlap.
