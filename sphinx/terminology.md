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

### 2.8 Flicker (1/f) noise parameters

Supported by `MOSFET_L1`, `MOSFET_3T`, `MOSFET_4T`, and
`MOSFET_subthreshold` when `include_noise` contains `"flicker"`.

| Code | Expanded name              | Definition                                                  |
| ---- | -------------------------- | ----------------------------------------------------------- |
| `KF` | Flicker noise coefficient  | Magnitude of the 1/f drain-current PSD. Default 0.          |
| `AF` | Drain-current exponent     | Dimensionless. Default 1.                                   |
| `EF` | Frequency exponent         | Dimensionless. Default 1 (pure 1/f); 2 → 1/f² roll-off.     |

PSD form: `S_I_D(f) = KF · I_op^AF / freq^EF`. The `freq` symbol is the
shared analysis-frequency symbol exposed from `sycan.mna`.

---

## 3. BJT (Gummel-Poon) parameters

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

### 3.4 Small-signal hybrid-π AC parameters

Linearised around `(I_C_op, I_B_op)`. `I_C_op` / `I_B_op` default to
per-instance symbols `I_C_op_<name>` / `I_B_op_<name>` — pass numeric
values (or DC-solve outputs) before evaluating an AC response.

| Code     | Expanded name                | Definition                                                         |
| -------- | ---------------------------- | ------------------------------------------------------------------ |
| `g_m`    | Transconductance             | `g_m = I_C_op / (NF · V_T)`.                                        |
| `r_pi`   | Base–emitter resistance      | `r_pi = BF / g_m`. Stamped as conductance `1/r_pi`.                |
| `r_o`    | Output resistance            | `r_o = VAF / I_C_op`. Omitted when `VAF = ∞`.                      |
| `C_pi`   | Base–emitter capacitance     | Diffusion + B–E depletion. Stamped as admittance `s · C_pi`.       |
| `C_mu`   | Base–collector capacitance   | B–C depletion (Miller path). Stamped as admittance `s · C_mu`.     |

### 3.5 Noise parameters

| Code       | Source            | PSD                                                          |
| ---------- | ----------------- | ------------------------------------------------------------ |
| shot (C–E) | `2 · q · I_C_op`  | Collector-current shot noise.                                |
| shot (B–E) | `2 · q · I_B_op`  | Base-current shot noise.                                     |
| `KF`, `AF` | flicker (B–E)     | `KF · I_B_op^AF / freq`. Enabled when `include_noise` lists `"flicker"` and `KF != 0`. |

---

## 4. Diode (Shockley)

### 4.1 DC and stamping

| Code  | Expanded name                                | Definition                                       |
| ----- | -------------------------------------------- | ------------------------------------------------ |
| `IS`  | Reverse-saturation current                   | A.                                               |
| `N`   | Ideality / emission coefficient              | Dimensionless; default 1.                        |
| `V_D` | Diode voltage                                | `V_D = V(anode) − V(cathode)`                    |
| `I_D` | Diode current (anode → cathode)              | `I_D = IS · (exp(V_D / (N · V_T)) − 1)`          |
| `I_op`| Operating-point current (for shot noise PSD) | A. Pass a value or use the auto-symbol.          |

### 4.2 Small-signal AC

| Code     | Expanded name              | Definition                                                                 |
| -------- | -------------------------- | -------------------------------------------------------------------------- |
| `V_D_op` | Operating-point diode bias | Defaults to per-instance symbol `V_D_op_<name>` if not supplied.            |
| `g_d`   | Small-signal conductance   | `g_d = ∂I_D/∂V_D = (IS / (N · V_T)) · exp(V_D_op / (N · V_T))`.            |
| `C_j`   | Junction capacitance       | Default 0. Stamped as admittance `s · C_j` in parallel with `g_d`.         |

---

## 5. JFET (Shichman-Hodges depletion-mode)

NJFET / PJFET — depletion-mode field-effect transistors. The model is
the same quadratic `(V_GS_eff + VTO)²` form as the L1 MOSFET in
saturation, but with the threshold expressed as `VTO` (positive
magnitude of the pinch-off voltage) and the device conducting at
`V_GS_eff = 0`.

| Code      | Expanded name                          | Definition / units                                                                      |
| --------- | -------------------------------------- | --------------------------------------------------------------------------------------- |
| `BETA`    | Transconductance parameter             | A/V². Plays the role of `(1/2)·μ·Cox·(W/L)` in the L1 MOSFET expression.                 |
| `VTO`     | Pinch-off / threshold magnitude        | Stored positive for both polarities (NJFET/PJFET).                                        |
| `LAMBDA`  | Channel-length modulation coefficient  | 1/V. `λ = 0` reproduces ideal long-channel.                                              |
| `C_gs`    | Gate-source capacitance                | Stamped as admittance `s · C_gs` in AC.                                                  |
| `C_gd`    | Gate-drain capacitance                 | Stamped as admittance `s · C_gd` in AC.                                                  |
| `V_GS_op`, `V_DS_op` | Operating-point bias        | Default to per-instance symbols if not supplied.                                          |
| `KF`, `AF`, `EF`     | Flicker noise parameters    | Same form as the MOSFET flicker noise (§ 2.8).                                           |

Saturation drain current (for `V_DS_eff ≥ V_ov`, `V_GS_eff ≥ −VTO`):

    I_D_mag = BETA · (V_GS_eff + VTO)² · (1 + LAMBDA · V_DS_eff)

Small-signal at the operating point: `g_m = ∂I_D/∂V_GS`,
`g_ds = ∂I_D/∂V_DS`. Thermal channel noise reuses the L1 MOSFET form
(`4 · k_B · T · γ · g_m`).

---

## 6. Vacuum-tube triode (Langmuir 3/2 power)

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

## 7. Voltage-controlled passive devices

### 7.1 Varactor (junction-capacitance model)

Voltage-controlled capacitor following the standard SPICE junction-
capacitance form:

    C(V) = C0 / (1 − V/V_J)^M

| Code   | Expanded name                                    | Definition                                                                       |
| ------ | ------------------------------------------------ | -------------------------------------------------------------------------------- |
| `C0`   | Zero-bias junction capacitance                   | F.                                                                                |
| `V_J`  | Junction (built-in) potential                    | V. Default 0.7.                                                                   |
| `M`    | Grading coefficient                              | Dimensionless. Default 0.5 (abrupt junction); 0.33 for linearly graded.            |
| `V_op` | Operating-point bias across the varactor         | Defaults to per-instance symbol `V_op_<name>` if not supplied.                    |

The varactor is stamped in AC as the admittance `s · C(V_op)`. It is
inert at DC.

### 7.2 VSwitch (voltage-controlled smooth switch)

Smooth (`tanh`-shaped) resistance whose value depends on a control-port
voltage `V_c`:

    R(V_c) = R_off + (R_on − R_off) · ½ · (1 + tanh((V_c − V_t) / V_h))

| Code    | Expanded name                       | Definition                                                                 |
| ------- | ----------------------------------- | -------------------------------------------------------------------------- |
| `R_on`  | On-state resistance                 | Ω. Default 1.                                                              |
| `R_off` | Off-state resistance                | Ω. Default 1 GΩ.                                                           |
| `V_t`   | Threshold control voltage           | V. The midpoint of the tanh transition.                                    |
| `V_h`   | Hysteresis-like transition width    | V. Smaller `V_h` → sharper switch. Default 0.1.                            |
| `V_c_op`| Operating-point control voltage     | Used to linearise the switch in AC.                                        |

The switch contributes a single conductance `1/R(V_c)` between the two
power-port nodes; the control port is a high-impedance pin and only
samples `V_c`.

---

## 8. Mutual inductance (`MutualCoupling`)

Couples two or more inductors via a shared coupling coefficient.

| Code | Expanded name                              | Definition                                                                                   |
| ---- | ------------------------------------------ | -------------------------------------------------------------------------------------------- |
| `k`  | Coupling coefficient                       | `0 ≤ k ≤ 1`; `k = 1` is perfect coupling. Default 1.                                          |
| `M`  | Mutual inductance (derived per pair)       | `M_ij = k · √(L_i · L_j)`, computed from each pair of named inductors at stamping time.       |

In SPICE the same primitive is the `K` element. The `MutualCoupling`
component takes a list of inductor names; SYCAN stamps the off-diagonal
inductance entries in MNA so the mutual flux shows up as the
expected voltage `M_ij · ∂I_j/∂t` (i.e. `s · M_ij · I_j` in AC).

---

## 9. Transmission line

| Code   | Expanded name                | Definition                                                                       |
| ------ | ---------------------------- | -------------------------------------------------------------------------------- |
| `Z0`   | Characteristic impedance     | Ω. Real for the lossless model.                                                   |
| `td`   | One-way time delay           | s. `td = ℓ / v` for line length ℓ and phase velocity v.                          |
| `loss` | Total attenuation (lossy)    | Nepers. Default 0 (lossless). The propagation `θ = loss + s · td`.               |
| `θ`    | Electrical length `theta`    | `θ = s · td` for the lossless line; `θ = loss + s · td` when lossy.              |
| `γ`    | Propagation constant         | `γ · ℓ = (α + s/v) · ℓ`, with the lossless case setting `α = 0`.                  |

ABCD form (with `θ = loss + s·td` covering both lossless and lossy
cases):

    [V1]   [ cosh(θ)         Z0 · sinh(θ) ] [ V2 ]
    [I1] = [ sinh(θ)/Z0      cosh(θ)      ] [-I2 ]

Y-matrix entries use `coth(θ)/Z0` (self) and `−csch(θ)/Z0` (mutual).

---

## 10. Controlled sources (basic two-port primitives)

| Code   | Expanded name                          | SPICE form                | Stamping behaviour                                         |
| ------ | -------------------------------------- | ------------------------- | ---------------------------------------------------------- |
| `VCVS` | Voltage-Controlled Voltage Source      | `Exxx N+ N- NC+ NC- gain` | `V(n+) − V(n−) = gain · (V(nc+) − V(nc−))`. `gain` is dimensionless. |
| `VCCS` | Voltage-Controlled Current Source      | `Gxxx N+ N- NC+ NC- gain` | Drives `gain · (V(nc+) − V(nc−))` from n+ to n−. `gain` is a transconductance [S]. |
| `CCVS` | Current-Controlled Voltage Source      | `Hxxx N+ N- VCTRL gain`   | `V(n+) − V(n−) = gain · I(ctrl)`. `gain` is a trans-resistance [Ω]. |
| `CCCS` | Current-Controlled Current Source      | `Fxxx N+ N- VCTRL gain`   | Drives `gain · I(ctrl)` from n+ to n−. `gain` is dimensionless.    |

---

## 11. Behavioral sources (B-element)

Arbitrary-expression sources. The expression `expr` may reference any
node-voltage symbol (`V(node)`); the components linearise it
automatically for AC and noise analyses.

| Component              | Constraint enforced                    | Notes                                                                                                                                  |
| ---------------------- | -------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| `BehavioralCurrent`    | Injects `expr` from `n_plus` to `n_minus` | Acts like a non-linear current source. `V_op_subs` (optional) supplies operating-point substitutions used during AC linearisation.     |
| `BehavioralVoltage`    | Forces `V(n_plus) − V(n_minus) = expr` | Acts like a non-linear voltage source; carries an auxiliary branch-current unknown. `V_op_subs` plays the same role as above.          |

Both are noise-less by construction (`SUPPORTED_NOISE = frozenset()`).

---

## 12. Signal-flow blocks (`sycan.components.blocks`)

Higher-level abstract blocks, useful for behavioural top-down design
(PLLs, ΣΔ modulators, etc.). Each is a single ideal voltage-mode I/O
block with differential ports `(in_p, in_m)` → `(out_p, out_m)` unless
stated otherwise.

| Block               | Constraint                                                                              | Parameters                                                              |
| ------------------- | ---------------------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| `Gain`              | `V(out_p) − V(out_m) = k · (V(in_p) − V(in_m))`                                          | `k` (voltage gain).                                                     |
| `Integrator`        | `H(s) = k / (s + leak)`                                                                 | `k` (DC gain numerator), `leak` (damping; 0 → ideal integrator), `var`. |
| `Summer`            | `V(out_p) − V(out_m) = Σ wᵢ · (V(in_p,i) − V(in_m,i))`                                   | `inputs` — list of `(in_p, in_m, weight)` tuples.                       |
| `Quantizer`         | `V(out_p) − V(out_m) = k_q · (V(in_p) − V(in_m)) + V_q`                                  | `k_q` (linear quantizer gain), plus an additive symbolic noise input.   |
| `TransferFunction`  | `V(out_p) − V(out_m) = H(s) · (V(in_p) − V(in_m))`                                       | `H` (Laplace expression), `var` (default `s`).                          |
| `OPAMP`             | Ideal single-VCVS op-amp: `V(out) = A · (V(in_p) − V(in_m))`                             | `A` (open-loop gain).                                                   |
| `OPAMP1`            | First-order dominant-pole op-amp: `H(s) = A · ω_p / (s + ω_p)`, with `ω_p = 2π·GBW / A`  | `A` (DC open-loop gain), `GBW` (gain-bandwidth product, Hz), `Z_out`.   |

`OPAMP` and `OPAMP1` are implemented as `SubCircuit`s — see § 13.

### 12.1 OPAMP1 derived quantities

| Code     | Expanded name              | Definition                                                                |
| -------- | -------------------------- | ------------------------------------------------------------------------- |
| `A`      | DC open-loop gain          | `H(0)` of the OPAMP1 transfer function.                                  |
| `GBW`    | Gain-bandwidth product     | Hz. Unity-gain frequency.                                                |
| `ω_p`    | Dominant pole              | `ω_p = 2π · GBW / A`.                                                     |
| `Z_out`  | Output impedance           | Optional series output Z. `None` ⇒ ideal voltage output.                  |

---

## 13. SubCircuit (`sycan.components.blocks.subcircuit`)

Hierarchical instantiation of a body `Circuit` into a parent. The
`port_map` dict maps each pin name in the inner body to its external
parent-circuit node. At stamping time the `SubCircuit` flattens its
body into the parent MNA system with name-mangled internal nodes, so a
sub-circuit instance contributes the same equations its body would have
contributed if pasted in line.

| Code        | Expanded name                                   | Notes                                             |
| ----------- | ----------------------------------------------- | ------------------------------------------------- |
| `body`      | Inner `Circuit`                                 | Defines the reusable block.                       |
| `port_map`  | `{body_pin: parent_node}` mapping                | Names every external connection point of the block.|

---

## 14. Voltage source waveforms (`VoltageSource`)

In addition to a DC `value` and an AC-phasor `ac_value`, a voltage
source may declare a time-domain `waveform`, whose Laplace transform is
stamped on the right-hand side of the MNA system in `ac` mode.

| Waveform | Parameters                                       | Laplace transform                                                                           |
| -------- | ------------------------------------------------ | ------------------------------------------------------------------------------------------- |
| `"sine"` | `amplitude`, `frequency`, `phase`                | `A · (s · sin(φ) + ω · cos(φ)) / (s² + ω²)` with `ω = 2π·frequency`.                         |
| `"pulse"`| `v1`, `v2`, `td`, `pw`                           | Single rectangular pulse: `v1/s + (v2−v1)·(exp(−s·td) − exp(−s·(td+pw)))/s`.                |
| `"exp"`  | `v1`, `v2`, `td1`, `tau1`, `td2?`, `tau2?`       | Exponential pulse rising toward `v2` with `tau1`, optionally recovering to `v1` with `tau2`. |

DC values are picked up automatically from the waveform parameters
(e.g. `value = v1` for `pulse`/`exp`).

---

## 15. MNA & analysis terms

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
| `has_nonlinear`  | Class flag: component contributes transcendental KCL terms via `stamp_nonlinear` (diode, BJT, MOSFET, JFET, triode, behavioral, varactor, vswitch). |
| `G_MIN`          | SPICE GMIN shunt — a 1 GΩ conductance from every node to ground used during damped Newton to keep the Jacobian conditioned at flat operating points. |
| `freq`           | Real-valued frequency symbol exposed by `sycan.mna`. Used by flicker-noise PSDs (which carry `1/freq^EF`) since `s = jω` is not a useful argument for a real PSD. |

### 15.1 Analysis modes

| Mode | Component behaviour                                                                                     |
| ---- | ------------------------------------------------------------------------------------------------------- |
| `dc` | Capacitors → open, inductors → 0 V source (short via auxiliary current). Nonlinear models contribute residuals. |
| `ac` | Small-signal Laplace-domain stamps. Capacitors → `s · C`, inductors → `1 / (s · L)`. Nonlinear devices → linearised at their operating point (`g_m`, `g_ds`, intrinsic caps). |

### 15.2 Solvers (`sycan.mna`)

| Solver               | Purpose                                                                                                                |
| -------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| `solve_dc`           | DC operating point. Linear systems → symbolic LU; nonlinear → CAS solver on full residual system.                      |
| `solve_dc_sweep`     | Sweep one symbolic `parameter` across `values` and substitute into the symbolic DC solution. Closed-form `.DC`.        |
| `solve_ac`           | Small-signal Laplace-domain solve. Returns `{Symbol(V(node)): expr(s)}`.                                                |
| `solve_tf`           | Transfer function `H(s) = V(output_node) / source_value`. Returns dict with `H`, `dc_gain`, `hf_gain`, `numerator`, `denominator`. |
| `solve_pz`           | Pole-zero analysis: factors `H(s)` to extract poles (denominator roots) and zeros (numerator roots). Returns `PZResult`. |
| `solve_impedance`    | Port impedance (input/output) at a labelled port, with `termination="auto"` choosing excitation/loading.                |
| `solve_noise`        | Output-referred noise PSD. Returns `(S_total, per_source)` — `S_total` and a dict of per-noise-source contributions.    |
| `solve_sensitivity`  | `∂V_out/∂p` for each parameter, in DC or AC mode. `normalized=True` reports `(p / V_out) · ∂V_out/∂p`.                  |

`PZResult` is a small record carrying `H`, `numerator`, `denominator`,
`poles`, and `zeros`.

---

## 16. ERC / DRC (`sycan.check`)

Read-only structural checks on a `Circuit`. `check_circuit(circuit)`
returns an `ERCReport`.

| Code              | Severity | Meaning                                                                       |
| ----------------- | -------- | ----------------------------------------------------------------------------- |
| `DUPLICATE_NAME`  | error    | Two components share a designator after sub-circuit flattening.               |
| `PIN_SHORT`       | warning  | Two pins of the same component land on the same node (self-loop / pin tie).   |
| `DANGLING_NODE`   | warning  | A node is referenced by exactly one pin — almost always a typo.               |
| `NO_GROUND`       | error    | No ground node (`"0"` / `GND`) exists in the flattened netlist.               |
| `FLOATING_GROUND` | error    | A pin connects to ground but ground itself isn't tied to anything else.       |
| `ISLAND`          | warning  | Two or more disjoint connected components — the netlist isn't one circuit.    |

`ERCReport` exposes:

| Attribute     | Meaning                                                                |
| ------------- | ---------------------------------------------------------------------- |
| `findings`    | List of `ERCFinding(severity, code, message)`.                         |
| `errors`      | Findings with `severity == "error"`.                                   |
| `warnings`    | Findings with `severity == "warning"`.                                 |
| `ok`          | `True` iff there are no `error`-severity findings.                     |

---

## 17. Network parameters (`sycan.network_params`)

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

## 18. Noise

| Code        | Expanded                                                                                                |
| ----------- | ------------------------------------------------------------------------------------------------------- |
| `PSD`       | Power Spectral Density [V²/Hz or A²/Hz].                                                                |
| `S_V_out`   | Output-voltage noise PSD.                                                                                |
| `H_k(s)`    | Trans-impedance from the k-th unit-current noise source to the output node.                              |
| `S_k`       | One-sided current-noise PSD of the k-th source.                                                          |
| `γ` (noise) | Long-channel channel-thermal-noise excess factor; `_NOISE_GAMMA = 2/3` in the L1/4T MOSFETs.            |
| `thermal`   | Johnson-Nyquist thermal noise. Resistor PSD: `4 · k_B · T / R`. MOSFET / JFET channel PSD: `4 · k_B · T · γ · g_m`. |
| `shot`      | Schottky shot noise, `2 · q · I_op` (one-sided). Used by Diode, BJT (×2), sub-threshold MOSFET.         |
| `flicker`   | 1/f noise: `KF · I_op^AF / freq^EF`. Emitted by the L1/3T/4T/subthreshold MOSFETs, JFET, and BJT.       |

The noise superposition formula used by `solve_noise`:

    S_V_out(s) = Σ_k  H_k(s) · H_k(−s) · S_k

---

## 19. Filter prototypes (`sycan.polynomials`)

All return `(numerator, denominator)` in the Laplace variable `s`,
normalised to `|H(0)| = 1` and 1 rad/s cutoff.

| Function       | Family                                                                  |
| -------------- | ----------------------------------------------------------------------- |
| `butterworth`  | Butterworth — maximally flat magnitude in the passband.                 |
| `chebyshev1`   | Chebyshev Type I — equiripple in the passband, monotonic in the stopband. |
| `bessel`       | Bessel — maximally flat group delay (linear phase).                     |

---

## 20. Headroom analysis (`sycan.headroom`)

| Term                    | Meaning                                                                                                  |
| ----------------------- | -------------------------------------------------------------------------------------------------------- |
| Headroom                | Range of an input variable (a single source value or a coupled group) over which **every** MOSFET stays in saturation. |
| `HeadroomResult`        | Returned record with `intervals`, `boundaries`, `samples`, `widest`, and `binding_devices()`.            |
| Binding device          | The transistor that exits saturation at a given interval edge — the one *setting* the headroom on that side. |
| `V_id`                  | Differential input voltage in a differential-pair sweep (typical use case in the headroom docstring).    |

---

## 21. Acronym quick-reference

| Acronym   | Expanded                                                                                  |
| --------- | ----------------------------------------------------------------------------------------- |
| `MOSFET`  | Metal-Oxide-Semiconductor Field-Effect Transistor.                                        |
| `NMOS`    | n-channel MOSFET.                                                                         |
| `PMOS`    | p-channel MOSFET.                                                                         |
| `JFET`    | Junction Field-Effect Transistor.                                                         |
| `NJFET` / `PJFET` | n-channel / p-channel JFET.                                                       |
| `BJT`     | Bipolar Junction Transistor.                                                              |
| `NPN`/`PNP` | BJT polarities; npn = n-emitter/p-base/n-collector, pnp the opposite.                    |
| `L1`      | Shichman-Hodges Level 1 — the original SPICE long-channel quadratic MOSFET model.         |
| `3T` / `4T` | Three-terminal (bulk tied to source) / four-terminal (bulk exposed) MOSFET wrapper.     |
| `MNA`     | Modified Nodal Analysis.                                                                  |
| `ERC`     | Electrical Rule Check (`sycan.check.check_circuit`).                                      |
| `DRC`     | Design Rule Check — used interchangeably with ERC in this codebase.                       |
| `SPICE`   | Simulation Program with Integrated Circuit Emphasis. Reference for sign conventions and parameter names. |
| `DC`      | Direct-current operating point.                                                           |
| `AC`      | Small-signal alternating-current (Laplace) analysis.                                       |
| `TF`      | Transfer Function (`solve_tf`).                                                           |
| `PZ`      | Pole-Zero analysis (`solve_pz`).                                                          |
| `GBW`     | Gain-bandwidth product (OPAMP1).                                                          |
| `RF`      | Radio-frequency (the `components.rf` package — currently the lossless transmission line). |
| `TLINE`   | Transmission line.                                                                        |
| `VCO`/`Vctrl` | Naming convention for the controlling source of a CCCS/CCVS.                          |
| `VCVS` / `VCCS` / `CCVS` / `CCCS` | Voltage- / Current-Controlled Voltage / Current Source primitives.    |
| `ABCD`    | Chain / transmission matrix.                                                              |
| `PSD`     | Power Spectral Density.                                                                   |
| `GMIN`    | SPICE shunt conductance from every node to ground used to keep the Jacobian conditioned. |
| `KF` / `AF` / `EF` | Flicker (1/f) noise coefficient / current exponent / frequency exponent.         |
| `B-element` | Behavioral source (`BehavioralVoltage`, `BehavioralCurrent`).                           |
