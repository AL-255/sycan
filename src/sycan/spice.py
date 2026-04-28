"""Minimal SPICE netlist parser for DC circuits.

Supported syntax:

* first line is a title and is discarded
* lines starting with ``*`` are comments; text after ``;`` is trimmed
* lines starting with ``+`` are continuations of the previous element
* ``.end`` stops parsing; other dot-directives are ignored
* elements::

      Rxxx  N+ N- value
      Lxxx  N+ N- value            ; inductor (DC short, AC 1/(sL))
      Cxxx  N+ N- value            ; capacitor (DC open, AC sC)
      Vxxx  N+ N- [DC dcval] [AC acval]
      Ixxx  N+ N- [DC dcval] [AC acval]
      Exxx  N+ N- NC+ NC- gain     ; VCVS
      Gxxx  N+ N- NC+ NC- gain     ; VCCS
      Fxxx  N+ N- VNAM gain        ; CCCS
      Hxxx  N+ N- VNAM gain        ; CCVS
      Wxxx  N1 N2                  ; ideal wire (stamped as a 0 V source)
      Mxxx  D G S TYPE mu_n Cox W L V_TH [m [V_T]]
                                                    ; TYPE=N/PMOS_subthreshold
      Mxxx  D G S TYPE mu_n Cox W L V_TH [lam [V_GS_op V_DS_op [C_gs [C_gd]]]]
                                                    ; TYPE=N/PMOS_L1
      Qxxx  C B E TYPE IS BF BR [V_T [VAF]]         ; TYPE=NPN or PNP (G-P)
      Dxxx  A K IS [N [V_T]]                        ; Shockley diode
      Pxxx  N+ N- [role]                            ; named port (role=input/output/generic)
      Txxx  N1+ N1- N2+ N2- Z0 td                   ; lossless transmission line
      Xxxx  P G K TRIODE K mu [V_g_op V_p_op [C_gk C_gp C_pk]]
                                                    ; vacuum-tube triode subcircuit
      GND[n] NODE                  ; ties NODE to the absolute zero reference

Values may be plain numbers with an engineering suffix
(``T G MEG K M U N P F``, case-insensitive) plus arbitrary trailing unit
letters, or a bare identifier that becomes a sympy symbol.
"""
from __future__ import annotations

import re
from pathlib import Path

import sympy as sp

from sycan.circuit import Circuit

_SUFFIXES = {
    "t": sp.Integer(10) ** 12,
    "g": sp.Integer(10) ** 9,
    "meg": sp.Integer(10) ** 6,
    "k": sp.Integer(10) ** 3,
    "m": sp.Rational(1, 10**3),
    "u": sp.Rational(1, 10**6),
    "n": sp.Rational(1, 10**9),
    "p": sp.Rational(1, 10**12),
    "f": sp.Rational(1, 10**15),
}

_GND_RE = re.compile(r"^gnd\d*$", re.IGNORECASE)

_NUMBER_RE = re.compile(
    r"""^
    (?P<mant>[+-]?(?:\d+\.\d*|\.\d+|\d+))
    (?:[eE](?P<exp>[+-]?\d+))?
    (?P<suffix>meg|[tgkmunpf])?
    [a-z]*
    $
    """,
    re.VERBOSE | re.IGNORECASE,
)


def parse_value(token: str) -> sp.Expr:
    """Convert a SPICE value token into a sympy expression.

    Numeric tokens accept engineering suffixes and arbitrary trailing
    unit letters. Non-numeric tokens are returned as ``sp.Symbol``.
    """
    m = _NUMBER_RE.match(token)
    if m is None:
        return sp.Symbol(token)
    value = sp.Rational(m.group("mant"))
    if m.group("exp"):
        value *= sp.Integer(10) ** int(m.group("exp"))
    if m.group("suffix"):
        value *= _SUFFIXES[m.group("suffix").lower()]
    return value


def _preprocess(text: str) -> list[tuple[int, str]]:
    """Return ``(lineno, content)`` pairs after stripping comments, the
    title line, blank lines, and folding ``+`` continuations."""
    out: list[tuple[int, str]] = []
    for i, raw in enumerate(text.splitlines(), 1):
        if i == 1:
            continue  # SPICE title line
        line = raw.split(";", 1)[0].strip()
        if not line or line.startswith("*"):
            continue
        if line.startswith("+"):
            if not out:
                raise ValueError(f"line {i}: continuation with no preceding element")
            prev_i, prev = out[-1]
            out[-1] = (prev_i, f"{prev} {line[1:].strip()}")
            continue
        out.append((i, line))
    return out


def _source_values(
    tokens: list[str], lineno: int, name: str
) -> tuple[sp.Expr, sp.Expr | None]:
    """Parse the DC and AC values of a V/I source.

    Accepts::

        <val>                       -> (val, None)   ; applies in both modes
        DC <val>                    -> (val, 0)      ; DC bias, short at AC
        AC <val>                    -> (0,   val)
        DC <val> AC <val>           -> (dc, ac)
        AC <val> DC <val>           -> (dc, ac)
    """
    dc_val: sp.Expr | None = None
    ac_val: sp.Expr | None = None
    dc_explicit = False
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        low = tok.lower()
        if low in ("dc", "ac"):
            if i + 1 >= len(tokens):
                raise ValueError(
                    f"line {lineno}: source {name!r} missing value after {tok!r}"
                )
            val = parse_value(tokens[i + 1])
            if low == "dc":
                dc_val = val
                dc_explicit = True
            else:
                ac_val = val
            i += 2
        else:
            if dc_val is not None:
                # Ignore trailing transient specs etc.
                break
            dc_val = parse_value(tok)
            i += 1
    if dc_val is None and ac_val is None:
        raise ValueError(f"line {lineno}: source {name!r} missing value")
    if dc_val is None:
        dc_val = sp.Integer(0)
    # Explicit DC keyword without AC means this is a DC-only source (AC short).
    if dc_explicit and ac_val is None:
        ac_val = sp.Integer(0)
    return dc_val, ac_val


def _require(parts: list[str], count: int, lineno: int, name: str) -> None:
    if len(parts) < count:
        raise ValueError(
            f"line {lineno}: element {name!r} needs at least {count} tokens, got {len(parts)}"
        )


def parse(text: str) -> Circuit:
    """Parse a SPICE netlist string into a :class:`Circuit`."""
    circuit = Circuit()
    for lineno, line in _preprocess(text):
        parts = line.split()
        name = parts[0]
        head = name[0].lower()

        if name.lower() == ".end":
            break
        if name.startswith("."):
            continue

        if _GND_RE.match(name):
            _require(parts, 2, lineno, name)
            circuit.add_gnd(name, parts[1])
            continue

        if head == "r":
            _require(parts, 4, lineno, name)
            circuit.add_resistor(name, parts[1], parts[2], parse_value(parts[3]))
        elif head == "l":
            _require(parts, 4, lineno, name)
            circuit.add_inductor(name, parts[1], parts[2], parse_value(parts[3]))
        elif head == "c":
            _require(parts, 4, lineno, name)
            circuit.add_capacitor(name, parts[1], parts[2], parse_value(parts[3]))
        elif head == "v":
            _require(parts, 4, lineno, name)
            dc_val, ac_val = _source_values(parts[3:], lineno, name)
            circuit.add_vsource(name, parts[1], parts[2], dc_val, ac_val)
        elif head == "i":
            _require(parts, 4, lineno, name)
            dc_val, ac_val = _source_values(parts[3:], lineno, name)
            circuit.add_isource(name, parts[1], parts[2], dc_val, ac_val)
        elif head == "e":
            _require(parts, 6, lineno, name)
            circuit.add_vcvs(
                name, parts[1], parts[2], parts[3], parts[4], parse_value(parts[5])
            )
        elif head == "g":
            _require(parts, 6, lineno, name)
            circuit.add_vccs(
                name, parts[1], parts[2], parts[3], parts[4], parse_value(parts[5])
            )
        elif head == "f":
            _require(parts, 5, lineno, name)
            circuit.add_cccs(name, parts[1], parts[2], parts[3], parse_value(parts[4]))
        elif head == "h":
            _require(parts, 5, lineno, name)
            circuit.add_ccvs(name, parts[1], parts[2], parts[3], parse_value(parts[4]))
        elif head == "w":
            # Ideal wire: a 0 V voltage source electrically merges the nodes.
            _require(parts, 3, lineno, name)
            circuit.add_vsource(name, parts[1], parts[2], 0)
        elif head == "p":
            _require(parts, 3, lineno, name)
            n_plus, n_minus = parts[1], parts[2]
            role = parts[3] if len(parts) > 3 else "generic"
            circuit.add_port(name, n_plus, n_minus, role)
        elif head == "t":
            _require(parts, 7, lineno, name)
            circuit.add_tline(
                name, parts[1], parts[2], parts[3], parts[4],
                parse_value(parts[5]), parse_value(parts[6]),
            )
        elif head == "d":
            _require(parts, 4, lineno, name)
            anode, cathode = parts[1], parts[2]
            IS_val = parse_value(parts[3])
            N_val = parse_value(parts[4]) if len(parts) > 4 else None
            V_T = parse_value(parts[5]) if len(parts) > 5 else None
            circuit.add_diode(name, anode, cathode, IS_val, N_val, V_T)
        elif head == "q":
            _require(parts, 8, lineno, name)
            collector, base, emitter, btype = (
                parts[1], parts[2], parts[3], parts[4]
            )
            if btype.upper() not in ("NPN", "PNP"):
                raise ValueError(
                    f"line {lineno}: unknown BJT type {btype!r}; "
                    "expected NPN or PNP"
                )
            IS_val = parse_value(parts[5])
            BF = parse_value(parts[6])
            BR = parse_value(parts[7])
            extra: dict[str, sp.Expr] = {}
            if len(parts) > 8:
                extra["V_T"] = parse_value(parts[8])
            if len(parts) > 9:
                extra["VAF"] = parse_value(parts[9])
            circuit.add_bjt(
                name, collector, base, emitter, btype.upper(),
                IS_val, BF, BR, **extra,
            )
        elif head == "m":
            _require(parts, 10, lineno, name)
            # Four-terminal MOSFETs slot a ``bulk`` node between
            # ``source`` and the model-type keyword, so detect them by
            # peeking at parts[5] before consuming the standard 3-node
            # layout. Everything else (L1, subthreshold, 3T) keeps the
            # original parts[4] = model layout.
            if len(parts) > 5 and parts[5].lower() in ("nmos_4t", "pmos_4t"):
                _require(parts, 11, lineno, name)
                drain, gate, source, bulk, mtype = (
                    parts[1], parts[2], parts[3], parts[4], parts[5],
                )
                mu_n  = parse_value(parts[6])
                Cox   = parse_value(parts[7])
                W     = parse_value(parts[8])
                L     = parse_value(parts[9])
                V_TH0 = parse_value(parts[10])
                mtype_lc = mtype.lower()
                kwargs: dict[str, sp.Expr] = {}
                opt_names = (
                    "lam", "gamma", "phi", "m", "V_T",
                    "V_GS_op", "V_DS_op", "V_BS_op",
                    "C_gs", "C_gd",
                )
                for i, key in enumerate(opt_names):
                    idx = 11 + i
                    if len(parts) > idx:
                        kwargs[key] = parse_value(parts[idx])
                adder = (
                    circuit.add_nmos_4t
                    if mtype_lc == "nmos_4t"
                    else circuit.add_pmos_4t
                )
                adder(
                    name, drain, gate, source, bulk,
                    mu_n, Cox, W, L, V_TH0, **kwargs,
                )
                continue
            drain, gate, source, mtype = parts[1], parts[2], parts[3], parts[4]
            mu_n = parse_value(parts[5])
            Cox = parse_value(parts[6])
            W = parse_value(parts[7])
            L = parse_value(parts[8])
            V_TH = parse_value(parts[9])
            mtype_lc = mtype.lower()
            if mtype_lc in ("nmos_subthreshold", "pmos_subthreshold"):
                m_val = parse_value(parts[10]) if len(parts) > 10 else None
                V_T = parse_value(parts[11]) if len(parts) > 11 else None
                adder = (
                    circuit.add_nmos_subthreshold
                    if mtype_lc == "nmos_subthreshold"
                    else circuit.add_pmos_subthreshold
                )
                adder(
                    name, drain, gate, source, mu_n, Cox, W, L, V_TH, m_val, V_T
                )
            elif mtype_lc in ("nmos_l1", "pmos_l1"):
                kwargs: dict[str, sp.Expr] = {}
                if len(parts) > 10:
                    kwargs["lam"] = parse_value(parts[10])
                if len(parts) > 11:
                    kwargs["V_GS_op"] = parse_value(parts[11])
                if len(parts) > 12:
                    kwargs["V_DS_op"] = parse_value(parts[12])
                if len(parts) > 13:
                    kwargs["C_gs"] = parse_value(parts[13])
                if len(parts) > 14:
                    kwargs["C_gd"] = parse_value(parts[14])
                adder = (
                    circuit.add_nmos_l1
                    if mtype_lc == "nmos_l1"
                    else circuit.add_pmos_l1
                )
                adder(name, drain, gate, source, mu_n, Cox, W, L, V_TH, **kwargs)
            elif mtype_lc in ("nmos_3t", "pmos_3t"):
                kwargs: dict[str, sp.Expr] = {}
                # Parameter order matches add_nmos_3t / add_pmos_3t,
                # which mirror MOSFET_L1 with ``m`` and ``V_T`` slotted
                # in after the channel-length-modulation parameter.
                if len(parts) > 10:
                    kwargs["lam"] = parse_value(parts[10])
                if len(parts) > 11:
                    kwargs["m"] = parse_value(parts[11])
                if len(parts) > 12:
                    kwargs["V_T"] = parse_value(parts[12])
                if len(parts) > 13:
                    kwargs["V_GS_op"] = parse_value(parts[13])
                if len(parts) > 14:
                    kwargs["V_DS_op"] = parse_value(parts[14])
                if len(parts) > 15:
                    kwargs["C_gs"] = parse_value(parts[15])
                if len(parts) > 16:
                    kwargs["C_gd"] = parse_value(parts[16])
                adder = (
                    circuit.add_nmos_3t
                    if mtype_lc == "nmos_3t"
                    else circuit.add_pmos_3t
                )
                adder(name, drain, gate, source, mu_n, Cox, W, L, V_TH, **kwargs)
            else:
                raise ValueError(
                    f"line {lineno}: unknown MOSFET model {mtype!r}; "
                    "expected N/PMOS_subthreshold, N/PMOS_L1, "
                    "N/PMOS_3T, or N/PMOS_4T"
                )
        elif head == "x":
            _require(parts, 5, lineno, name)
            subckt = parts[4].upper()
            if subckt == "TRIODE":
                _require(parts, 7, lineno, name)
                plate, grid, cathode = parts[1], parts[2], parts[3]
                K_val = parse_value(parts[5])
                mu_val = parse_value(parts[6])
                kwargs: dict[str, sp.Expr] = {}
                if len(parts) > 7:
                    kwargs["V_g_op"] = parse_value(parts[7])
                if len(parts) > 8:
                    kwargs["V_p_op"] = parse_value(parts[8])
                if len(parts) > 9:
                    kwargs["C_gk"] = parse_value(parts[9])
                if len(parts) > 10:
                    kwargs["C_gp"] = parse_value(parts[10])
                if len(parts) > 11:
                    kwargs["C_pk"] = parse_value(parts[11])
                circuit.add_triode(name, plate, grid, cathode, K_val, mu_val, **kwargs)
            else:
                raise ValueError(
                    f"line {lineno}: unknown subcircuit type {subckt!r} for {name!r}"
                )
        else:
            raise ValueError(f"line {lineno}: unsupported element {name!r}")

    return circuit


def parse_file(path: str | Path) -> Circuit:
    """Parse a SPICE netlist from a file path."""
    return parse(Path(path).read_text())
