from sycan import cas as cas
from sycan import Circuit, solve_pz

s = cas.Symbol("s")

print("=== Symbolic Pole-Zero Analysis Examples ===\n")

# --- Example 1: RC Lowpass ---
print("1) RC Lowpass Filter")
R, C = cas.symbols("R C", positive=True)
c1 = Circuit("RC Lowpass")
c1.add_vsource("Vin", "in", "0", 0, ac_value=1)
c1.add_resistor("R1", "in", "out", R)
c1.add_capacitor("C1", "out", "0", C)
pz1 = solve_pz(c1, "out", input_source="Vin", simplify=True)

print(f"$$H(s) = {cas.latex(pz1.H)}$$")
print("Pole:")
for p in pz1.poles:
    print(f"$$s = {cas.latex(p)}$$")
print("Zeros: none")
print()

# --- Example 2: RC Highpass ---
print("2) RC Highpass Filter")
c2 = Circuit("RC Highpass")
c2.add_vsource("Vin", "in", "0", 0, ac_value=1)
c2.add_capacitor("C1", "in", "out", C)
c2.add_resistor("R1", "out", "0", R)
pz2 = solve_pz(c2, "out", input_source="Vin", simplify=True)

print(f"$$H(s) = {cas.latex(pz2.H)}$$")
print("Pole:")
for p in pz2.poles:
    print(f"$$s = {cas.latex(p)}$$")
print("Zero:")
for z in pz2.zeros:
    print(f"$$s = {cas.latex(z)}$$")
print()

# --- Example 3: RLC Series (capacitor output) ---
print("3) RLC Series — output across capacitor")
L_ = cas.Symbol("L", positive=True)
c3 = Circuit("RLC Series")
c3.add_vsource("Vin", "in", "0", 0, ac_value=1)
c3.add_resistor("R1", "in", "mid", R)
c3.add_inductor("L1", "mid", "out", L_)
c3.add_capacitor("C1", "out", "0", C)
pz3 = solve_pz(c3, "out", input_source="Vin", simplify=True)

print(f"$$H(s) = {cas.latex(pz3.H)}$$")
print("Poles:")
for p in pz3.poles:
    print(f"$$s = {cas.latex(p)}$$")
print("Zeros: none (low-pass)")
print()

# --- Example 4: RLC Bandpass (output across resistor) ---
print("4) RLC Bandpass — output across resistor")
c4 = Circuit("RLC Bandpass")
c4.add_vsource("Vin", "in", "0", 0, ac_value=1)
c4.add_resistor("R1", "in", "out", R)
c4.add_inductor("L1", "out", "mid", L_)
c4.add_capacitor("C1", "mid", "0", C)
pz4 = solve_pz(c4, "out", input_source="Vin", simplify=True)

print(f"$$H(s) = {cas.latex(pz4.H)}$$")
print("Poles:")
for p in pz4.poles:
    print(f"$$s = {cas.latex(p)}$$")
print("Zeros:")
for z in pz4.zeros:
    print(f"$$s = {cas.latex(z)}$$")
print()

# --- Poles of standard forms ---
print("=== Standard pole locations ===\n")
print("RC lowpass pole:  $$s = -\\frac{1}{RC}$$")
print("RLC series (Q > 1/2):")
print("$$s = -\\frac{R}{2L} \\pm j \\sqrt{\\frac{1}{LC} - \\left(\\frac{R}{2L}\\right)^2}$$")
print("$$\\omega_0 = \\frac{1}{\\sqrt{LC}}, \\quad Q = \\frac{1}{R}\\sqrt{\\frac{L}{C}}$$")
