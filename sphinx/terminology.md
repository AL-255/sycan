# SYCAN terminology reference

A glossary of every short symbol, abbreviation and parameter name that
appears across SYCAN's component models, analyses and noise machinery.
Symbols are grouped by topic; each entry lists the source code spelling
followed by the fully-expanded mathematical form.

Conventions:

- `V_T` is the *thermal voltage* (`kT/q`), **not** the threshold voltage.
  The threshold is `V_TH`. Mixing these up is the single most common
  hazard in this codebase.
- `mu_n` is the in-code spelling of the channel-carrier mobility μ; for
  PMOS it physically corresponds to μₚ but the same field name is used.
- `pol` is `+1` for NMOS / NPN and `−1` for PMOS / PNP. All polarity-
  aware models compute `*_eff = pol · (terminal voltage)` so the code
  can be written once for both polarities.
- All currents use the SPICE sign convention: positive flowing **into**
  the named terminal.

---

## 1. Physical constants

| Symbol  | Code      | Meaning                                       | Typical value         |
| ------- | --------- | --------------------------------------------- | --------------------- |
| `k_B`   | `k_B`     | Boltzmann constant                            | 1.380649 × 10⁻²³ J/K |
| `T`     | `T`       | Absolute temperature                          | 300 K (≈27 °C)       |
| `q`     | `q`       | Elementary charge                             | 1.602176634 × 10⁻¹⁹ C |
| `V_T`   | `V_T`     | Thermal voltage `k_B · T / q`                 | ≈25.85 mV at 300 K    |

The default `_DEFAULT_VT = sp.Rational(2585, 100000)` baked into the BJT,
diode and sub-threshold MOSFET models is exactly this 25.85 mV value
(`sp` here is `sycan.cas`). `k_B`, `T`, `q` are exposed as CAS
`Symbol`s in `sycan.mna` so users can substitute numeric values when
evaluating noise PSDs.

---

## 2. MOSFET parameters

### 2.1 Geometry & process

| Code  | Expanded name                          | Definition / units                                                                                       |
| ----- | -------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| `W`   | Channel width                          | Drawn gate width [m].                                                                                     |
| `L`   | Channel length                         | Drawn gate length [m].                                                                                    |
| `Cox` | Oxide capacitance per unit area        | `C_ox = ε_ox / t_ox` [F/m²]. Gate-oxide specific capacitance.                                            |
| `mu_n`| Channel-carrier mobility               | μₙ for NMOS, μₚ for PMOS [m²/(V·s)]. Same field name used for both polarities.                            |
| `β`   | Transconductance parameter (derived)   | `β = μ · Cox · (W / L)` [A/V²]. Computed internally as `_beta`.                                          |

`Cox` is "C-oxide", *not* a capacitor named "Cox". It is the gate-oxide
capacitance per unit gate area, **not** a total capacitance — multiply
by `W · L` to get the absolute oxide capacitance of one device.

### 2.2 Threshold & body effect

| Code     | Expanded name                          | Definition                                                                                                                    |
| -------- | -------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| `V_TH`   | Threshold voltage (positive magnitude) | Gate-source bias at the strong-inversion boundary. Stored as a positive number for both NMOS and PMOS.                         |
| `V_TH0`  | Zero-bias threshold voltage            | `V_TH` evaluated at `V_SB = 0`. Used in 4T as the parameter; the body-effect term shifts it.                                  |
| `γ`      | Body-effect coefficient `gamma`        | √V. `V_TH(V_SB) = V_TH0 + γ · (√(2 φ_F + V_SB) − √(2 φ_F))`. Zero by default → bulk pin is cosmetic.                          |
| `2 φ_F`  | Surface potential at strong inversion (`phi`) | Default ≈ 0.7 V. φ_F is the Fermi potential of the bulk; the surface needs to bend by `2 φ_F` for strong inversion.       |
| `V_SB`   | Source-to-bulk voltage                 | `V_SB = V(source) − V(bulk)`, polarity-flipped internally so `V_SB ≥ 0` in physical operation.                                |

In the 3T wrappers (`NMOS_3T`, `PMOS_3T`) the bulk is tied to the source,
which forces `V_SB = 0` — the body-effect term vanishes and `V_TH` is
just `V_TH0`.

### 2.3 Channel-length modulation & operating-region model

| Code      | Expanded name                                | Definition                                                                                                  |
| --------- | -------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| `λ`       | Channel-length modulation parameter `lam`    | `1/V`. Saturation current carries the factor `(1 + λ · V_DS_eff)`; `λ = 0` reproduces ideal long-channel.   |
| `V_ov`    | Overdrive voltage                            | `V_ov = V_GS_eff − V_TH`. Often called `V_GS − V_TH` in textbooks.                                          |
| `m`       | Sub-threshold slope factor                   | `m = 1 + C_d / C_ox`. Default 1.5. Controls how steeply weak-inversion current rolls off with `V_GS`.       |
| `V_off`   | Strong/weak split point (segmented model)    | `V_off = V_TH + 2 · m · V_T`. Boundary between weak and strong inversion in the segmented MOSFET_3T/4T.     |
| `I_off`   | Boundary drain current                       | `I_off = 2 · β · (m · V_T)²`. The drain current at `V_GS_eff = V_off`; ensures C¹-smooth join.              |

### 2.4 Effective terminal voltages

| Code       | Definition                                      |
| ---------- | ----------------------------------------------- |
| `V_GS`     | `V(gate)   − V(source)` (un-polarised)          |
| `V_DS`     | `V(drain)  − V(source)` (un-polarised)          |
| `V_BS`     | `V(bulk)   − V(source)` (un-polarised)          |
| `V_GS_eff` | `pol · V_GS` — `pol = +1` (NMOS) / `−1` (PMOS)  |
| `V_DS_eff` | `pol · V_DS`                                    |
| `V_SB_eff` | `−pol · V_BS` (so `V_SB ≥ 0` physically)        |

### 2.5 Drain-current expressions

Strong-inversion saturation (Shichman-Hodges Level 1, with channel-
length modulation):

    I_D_mag = (1/2) · μ · Cox · (W/L) · (V_GS_eff − V_TH)² · (1 + λ · V_DS_eff)
    I_D     = pol · I_D_mag

Strong-inversion triode:

    I_D_mag = β · (V_ov · V_DS_eff − (1/2) · V_DS_eff²) · (1 + λ · V_DS_eff)

Weak inversion (sub-threshold, `MOSFET_subthreshold`):

    I_D_mag = μ · Cox · (W/L) · V_T²
              · exp((V_GS_eff − m · V_TH) / (m · V_T))
              · (1 − exp(−V_DS_eff / V_T))

Weak inversion (segmented 3T/4T form, joined to L1 at `V_off`):

    I_D_mag = I_off · exp((V_GS_eff − V_off) / (m · V_T))
              · (1 − exp(−V_DS_eff / V_T))
              · (1 + λ · V_DS_eff)

### 2.6 Operating regions

Reported by `operating_region()`:

| Region            | L1 condition                                  | 3T/4T condition                                |
| ----------------- | --------------------------------------------- | ---------------------------------------------- |
| `cutoff`          | `V_GS_eff ≤ V_TH`                             | (replaced by `weak_inversion` in 3T/4T)        |
| `weak_inversion`  | —                                             | `V_GS_eff < V_off`                             |
| `triode`          | `V_GS_eff > V_TH`, `V_DS_eff < V_GS_eff−V_TH` | `V_GS_eff ≥ V_off`, `V_DS_eff < V_GS_eff−V_TH` |
| `saturation`      | `V_GS_eff > V_TH`, `V_DS_eff ≥ V_GS_eff−V_TH` | `V_GS_eff ≥ V_off`, `V_DS_eff ≥ V_GS_eff−V_TH` |

### 2.7 Small-signal MOSFET parameters

| Code   | Expanded name              | Definition (evaluated at `(V_GS_op, V_DS_op, V_BS_op)`)             |
| ------ | -------------------------- | ------------------------------------------------------------------- |
| `g_m`  | Gate transconductance      | `∂I_D/∂V_GS` at the operating point.                                |
| `g_ds` | Drain output conductance   | `∂I_D/∂V_DS` at the operating point. Inverse `r_ds = 1/g_ds`.       |
| `g_mb` | Bulk transconductance      | `∂I_D/∂V_BS` at the operating point. Captures back-gate effect.     |
| `C_gs` | Gate-source capacitance    | Intrinsic; stamped as admittance `s · C_gs` in AC.                  |
| `C_gd` | Gate-drain capacitance     | Intrinsic; stamped as admittance `s · C_gd` in AC. Miller path.     |

`V_GS_op`, `V_DS_op`, `V_BS_op` are the symbolic *operating-point*
voltages — substitute concrete numbers (or DC-solve outputs) before
evaluating an AC response.

---

## 3. BJT (Gummel-Poon DC) parameters

### 3.1 Junction voltages and ideal transport currents

| Code   | Expanded name                            | Definition                                              |
| ------ | ---------------------------------------- | ------------------------------------------------------- |
| `V_BE` | Base-emitter voltage                     | `V_BE = pol · (V(base) − V(emitter))`                   |
| `V_BC` | Base-collector voltage                   | `V_BC = pol · (V(base) − V(collector))`                 |
| `I_BF` | Forward ideal transport current          | `I_BF = IS · (exp(V_BE / (NF · V_T)) − 1)`              |
| `I_BR` | Reverse ideal transport current          | `I_BR = IS · (exp(V_BC / (NR · V_T)) − 1)`              |
| `I_CE` | Collector-emitter transport current      | `I_CE = (I_BF − I_BR) / q_B`                            |

`pol = +1` for NPN, `−1` for PNP.

### 3.2 Model parameters

| Code  | Expanded name                                                        | Default (Ebers-Moll fall-back) |
| ----- | -------------------------------------------------------------------- | ------------------------------ |
| `IS`  | Saturation current                                                   | (must be supplied)             |
| `BF`  | Forward current gain (β_F)                                           | (must be supplied)             |
| `BR`  | Reverse current gain (β_R)                                           | (must be supplied)             |
| `NF`  | Forward emission coefficient (ideality factor of B–E ideal diode)    | 1                              |
| `NR`  | Reverse emission coefficient (ideality factor of B–C ideal diode)    | 1                              |
| `VAF` | Forward Early voltage                                                | ∞ (no Early effect)            |
| `VAR` | Reverse Early voltage                                                | ∞                              |
| `IKF` | Forward knee current (high-level injection roll-off)                 | ∞ (no roll-off)                |
| `IKR` | Reverse knee current                                                 | ∞                              |
| `ISE` | B–E leakage saturation current (non-ideal recombination diode)       | 0                              |
| `NE`  | B–E leakage emission coefficient                                     | 1.5                            |
| `ISC` | B–C leakage saturation current                                       | 0                              |
| `NC`  | B–C leakage emission coefficient                                     | 2                              |

### 3.3 Base-charge factor

| Code  | Definition                                                                 |
| ----- | -------------------------------------------------------------------------- |
| `q_1` | `1 / (1 − V_BC/VAF − V_BE/VAR)`. Early-effect term.                        |
| `q_2` | `I_BF/IKF + I_BR/IKR`. High-level injection term.                          |
| `q_B` | `(q_1 / 2) · (1 + sqrt(1 + 4 · q_2))`. Normalised majority base charge.    |

Terminal currents (positive into each terminal):

    I_C = pol · (I_CE − I_BC_total)
    I_B = pol · (I_BE_total + I_BC_total)
    I_E = −(I_C + I_B)

where `I_BE_total = I_BF/BF + ISE·(exp(V_BE/(NE·V_T)) − 1)` and
`I_BC_total = I_BR/BR + ISC·(exp(V_BC/(NC·V_T)) − 1)`.

---

## 4. Diode (Shockley)

| Code  | Expanded name                                | Definition                                       |
| ----- | -------------------------------------------- | ------------------------------------------------ |
| `IS`  | Reverse-saturation current                   | A.                                               |
| `N`   | Ideality / emission coefficient              | Dimensionless; default 1.                        |
| `V_D` | Diode voltage                                | `V_D = V(anode) − V(cathode)`                    |
| `I_D` | Diode current (anode → cathode)              | `I_D = IS · (exp(V_D / (N · V_T)) − 1)`          |
| `I_op`| Operating-point current (for shot noise PSD) | A. Pass a value or use the auto-symbol.          |

---

## 5. Vacuum-tube triode (Langmuir 3/2 power)

| Code | Expanded name              | Definition                                      |
| ---- | -------------------------- | ----------------------------------------------- |
| `K`  | Perveance                  | A / V^(3/2). Geometry-dependent constant.       |
| `μ`  | Amplification factor `mu`  | Dimensionless; obeys `μ = g_m · r_p = g_m / g_p`.|
| `V_gk` | Grid-cathode voltage     | `V(grid) − V(cathode)`                          |
| `V_pk` | Plate-cathode voltage    | `V(plate) − V(cathode)`                         |
| `I_p`  | Plate current            | `I_p = K · (μ · V_gk + V_pk)^(3/2)` (forward conduction only) |
| `g_p`  | Plate conductance        | `∂I_p / ∂V_pk`. Inverse `r_p = 1 / g_p` is plate resistance. |
| `g_m`  | Triode transconductance  | `∂I_p / ∂V_gk = (3/2) · K · μ · √(μ V_g_op + V_p_op)` |
| `C_gk`, `C_gp`, `C_pk` | Intrinsic interelectrode capacitances | Grid-cathode, grid-plate (Miller), plate-cathode. |

---

## 6. Transmission line (lossless)

| Code  | Expanded name                | Definition                                                      |
| ----- | ---------------------------- | --------------------------------------------------------------- |
| `Z0`  | Characteristic impedance     | Ω. Real for the lossless model.                                  |
| `td`  | One-way time delay           | s. `td = ℓ / v` for line length ℓ and phase velocity v.         |
| `θ`   | Electrical length `theta`    | `θ = s · td` in the Laplace domain.                             |
| `γ`   | Propagation constant (lossy) | `γ · ℓ = (α + s/v) · ℓ` — lossless case sets `α = 0`.            |

ABCD form of the lossless line:

    [V1]   [ cosh(s·td)         Z0 · sinh(s·td) ] [ V2 ]
    [I1] = [ sinh(s·td)/Z0      cosh(s·td)      ] [-I2 ]

Y-matrix entries use `coth(s·td)/Z0` (self) and `−csch(s·td)/Z0` (mutual).

---

## 7. Controlled sources (basic two-port primitives)

| Code   | Expanded name                          | SPICE form                | Stamping behaviour                                         |
| ------ | -------------------------------------- | ------------------------- | ---------------------------------------------------------- |
| `VCVS` | Voltage-Controlled Voltage Source      | `Exxx N+ N- NC+ NC- gain` | `V(n+) − V(n−) = gain · (V(nc+) − V(nc−))`. `gain` is dimensionless. |
| `VCCS` | Voltage-Controlled Current Source      | `Gxxx N+ N- NC+ NC- gain` | Drives `gain · (V(nc+) − V(nc−))` from n+ to n−. `gain` is a transconductance [S]. |
| `CCVS` | Current-Controlled Voltage Source      | `Hxxx N+ N- VCTRL gain`   | `V(n+) − V(n−) = gain · I(ctrl)`. `gain` is a trans-resistance [Ω]. |
| `CCCS` | Current-Controlled Current Source      | `Fxxx N+ N- VCTRL gain`   | Drives `gain · I(ctrl)` from n+ to n−. `gain` is dimensionless.    |

---

## 8. MNA & analysis terms

| Code / Term      | Expanded                                                                                          |
| ---------------- | ------------------------------------------------------------------------------------------------- |
| `MNA`            | Modified Nodal Analysis. Linear system `A · x = b` whose unknowns are node voltages plus a few branch currents. |
| `KCL`            | Kirchhoff's Current Law. The MNA row equations are KCL at each non-ground node.                   |
| `KVL`            | Kirchhoff's Voltage Law.                                                                          |
| `A`              | MNA matrix (admittances + auxiliary stamps).                                                      |
| `x`              | Unknown vector — node voltages followed by auxiliary branch currents (one per V/E/H source, etc.).|
| `b`              | Right-hand side — independent excitations.                                                        |
| `s`              | Laplace variable. Capacitor stamps `s · C`; inductor stamps `1 / (s · L)`.                        |
| `aux`            | Auxiliary branch current — extra unknown row used by elements that can't be stamped as plain admittances (V-source, VCVS/CCVS/CCCS, DC inductor, DC TLINE). |
| `has_nonlinear`  | Class flag: component contributes transcendental KCL terms via `stamp_nonlinear` (diode, BJT, MOSFET, triode). |
| `G_MIN`          | SPICE GMIN shunt — a 1 GΩ conductance from every node to ground used during damped Newton to keep the Jacobian conditioned at flat operating points. |

### 8.1 Analysis modes

| Mode | Component behaviour                                                                                     |
| ---- | ------------------------------------------------------------------------------------------------------- |
| `dc` | Capacitors → open, inductors → 0 V source (short via auxiliary current). Nonlinear models contribute residuals. |
| `ac` | Small-signal Laplace-domain stamps. Capacitors → `s · C`, inductors → `1 / (s · L)`. Nonlinear devices → linearised at their operating point (`g_m`, `g_ds`, intrinsic caps). |

---

## 9. Network parameters (`sycan.network_params`)

Two-port (and n-port where it applies) representations:

| Code   | Name                          | Defining relation                                  |
| ------ | ----------------------------- | -------------------------------------------------- |
| `Z`    | Impedance parameters          | `V = Z · I`                                        |
| `Y`    | Admittance parameters         | `I = Y · V`                                        |
| `S`    | Scattering parameters         | `b = S · a`, with reference impedance `Z0`         |
| `ABCD` | Chain (transmission) matrix   | `[V1; I1] = ABCD · [V2; −I2]` (2-port only)        |
| `T`    | Transfer / scattering-transfer | `[a1; b1] = T · [b2; a2]` (2-port only)            |

`a` and `b` are the incident and reflected normalised power waves; `Z0`
is the per-port reference impedance (default 50 Ω).

---

## 10. Noise

| Code        | Expanded                                                                                                |
| ----------- | ------------------------------------------------------------------------------------------------------- |
| `PSD`       | Power Spectral Density [V²/Hz or A²/Hz].                                                                |
| `S_V_out`   | Output-voltage noise PSD.                                                                                |
| `H_k(s)`    | Trans-impedance from the k-th unit-current noise source to the output node.                              |
| `S_k`       | One-sided current-noise PSD of the k-th source.                                                          |
| `γ` (noise) | Long-channel channel-thermal-noise excess factor; `_NOISE_GAMMA = 2/3` in the L1/4T MOSFETs.            |
| `thermal`   | Johnson-Nyquist thermal noise. Resistor PSD: `4 · k_B · T / R`. MOSFET channel PSD: `4 · k_B · T · γ · g_m`. |
| `shot`      | Schottky shot noise, `2 · q · I_op` (one-sided). Used by Diode, BJT (×2), sub-threshold MOSFET.         |
| `flicker`   | 1/f noise (recognised kind; not currently emitted by any built-in component).                            |

The noise superposition formula used by `solve_noise`:

    S_V_out(s) = Σ_k  H_k(s) · H_k(−s) · S_k

---

## 11. Filter prototypes (`sycan.polynomials`)

All return `(numerator, denominator)` in the Laplace variable `s`,
normalised to `|H(0)| = 1` and 1 rad/s cutoff.

| Function       | Family                                                                  |
| -------------- | ----------------------------------------------------------------------- |
| `butterworth`  | Butterworth — maximally flat magnitude in the passband.                 |
| `chebyshev1`   | Chebyshev Type I — equiripple in the passband, monotonic in the stopband. |
| `bessel`       | Bessel — maximally flat group delay (linear phase).                     |

---

## 12. Headroom analysis (`sycan.headroom`)

| Term                    | Meaning                                                                                                  |
| ----------------------- | -------------------------------------------------------------------------------------------------------- |
| Headroom                | Range of an input variable (a single source value or a coupled group) over which **every** MOSFET stays in saturation. |
| `HeadroomResult`        | Returned record with `intervals`, `boundaries`, `samples`, `widest`, and `binding_devices()`.            |
| Binding device          | The transistor that exits saturation at a given interval edge — the one *setting* the headroom on that side. |
| `V_id`                  | Differential input voltage in a differential-pair sweep (typical use case in the headroom docstring).    |

---

## 13. Acronym quick-reference

| Acronym   | Expanded                                                                                  |
| --------- | ----------------------------------------------------------------------------------------- |
| `MOSFET`  | Metal-Oxide-Semiconductor Field-Effect Transistor.                                        |
| `NMOS`    | n-channel MOSFET.                                                                         |
| `PMOS`    | p-channel MOSFET.                                                                         |
| `BJT`     | Bipolar Junction Transistor.                                                              |
| `NPN`/`PNP` | BJT polarities; npn = n-emitter/p-base/n-collector, pnp the opposite.                    |
| `L1`      | Shichman-Hodges Level 1 — the original SPICE long-channel quadratic MOSFET model.         |
| `3T` / `4T` | Three-terminal (bulk tied to source) / four-terminal (bulk exposed) MOSFET wrapper.     |
| `MNA`     | Modified Nodal Analysis.                                                                  |
| `SPICE`   | Simulation Program with Integrated Circuit Emphasis. Reference for sign conventions and parameter names. |
| `DC`      | Direct-current operating point.                                                           |
| `AC`      | Small-signal alternating-current (Laplace) analysis.                                       |
| `RF`      | Radio-frequency (the `components.rf` package — currently the lossless transmission line). |
| `TLINE`   | Transmission line.                                                                        |
| `VCO`/`Vctrl` | Naming convention for the controlling source of a CCCS/CCVS.                          |
| `ABCD`    | Chain / transmission matrix.                                                              |
| `PSD`     | Power Spectral Density.                                                                   |
| `GMIN`    | SPICE shunt conductance from every node to ground used to keep the Jacobian conditioned. |
