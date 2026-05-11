"""Minimal SPICE netlist parser (and writer) for DC / AC circuits.

Supported syntax:

* first line is a title and is discarded
* lines starting with ``*`` are comments; text after ``;`` is trimmed
* lines starting with ``+`` are continuations of the previous element
* ``.end`` stops parsing; other dot-directives are ignored
* ``.subckt name pin1 pin2 ... [PARAMS: k=v ...]`` /
  ``.ends [name]`` defines a reusable subcircuit. ``PARAMS:``
  declares default parameter values that body components may
  reference via plain symbols (``R1 in out R``). Instances override
  defaults via ``Xinst pin1 ... name PARAMS: k=v ...``. The body may
  itself contain ``X`` references to other user subcircuits or to
  the built-in ``OPAMP`` / ``TRIODE`` blocks. Nested ``.subckt`` *in
  source* is rejected; nest semantically by having one subckt
  instantiate another via its ``X`` element.
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
      Xxxx  IN+ IN- OUT OPAMP [A]                   ; ideal differential op-amp
      GND[n] NODE                  ; ties NODE to the absolute zero reference

Values may be plain numbers with an engineering suffix
(``T G MEG K M U N P F``, case-insensitive) plus arbitrary trailing unit
letters, or a bare identifier that becomes a sympy symbol.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from sycan import cas as cas

from sycan.circuit import Circuit

_SUFFIXES = {
    "t": cas.Integer(10) ** 12,
    "g": cas.Integer(10) ** 9,
    "meg": cas.Integer(10) ** 6,
    "k": cas.Integer(10) ** 3,
    "m": cas.Rational(1, 10**3),
    "u": cas.Rational(1, 10**6),
    "n": cas.Rational(1, 10**9),
    "p": cas.Rational(1, 10**12),
    "f": cas.Rational(1, 10**15),
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


def parse_value(token: str) -> cas.Expr:
    """Convert a SPICE value token into a sympy expression.

    Numeric tokens accept engineering suffixes and arbitrary trailing
    unit letters. Non-numeric tokens are returned as ``cas.Symbol``.
    """
    m = _NUMBER_RE.match(token)
    if m is None:
        return cas.Symbol(token)
    value = cas.Rational(m.group("mant"))
    if m.group("exp"):
        value *= cas.Integer(10) ** int(m.group("exp"))
    if m.group("suffix"):
        value *= _SUFFIXES[m.group("suffix").lower()]
    return value


@dataclass
class _SubcktDef:
    """A parsed ``.SUBCKT`` block awaiting instantiation.

    ``pins`` are the positional pin names declared on the ``.SUBCKT``
    line, in order. ``defaults`` carries the parameter defaults declared
    via ``PARAMS: k=v ...`` (if any). ``lines`` are the body lines
    (already preprocessed) — they get re-parsed into a body
    :class:`Circuit` the first time the subckt is referenced via an
    ``X`` element.
    """
    name: str
    pins: list[str]
    defaults: dict[str, cas.Expr] = field(default_factory=dict)
    lines: list[tuple[int, str]] = field(default_factory=list)


def _parse_param_assignments(tokens: list[str]) -> dict[str, cas.Expr]:
    """Parse a sequence of ``key=value`` tokens into a sympy-valued dict.

    Tokens may be glued (``R=1k``) or split across whitespace
    (``R = 1k`` → three tokens). Trailing ``params:`` keyword is
    consumed as a no-op for compatibility with SPICE source that
    emits the keyword separately from the assignments.
    """
    out: dict[str, cas.Expr] = {}
    i = 0
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        if tok.lower() == "params:":
            i += 1
            continue
        if "=" in tok:
            key, _, val = tok.partition("=")
            if val == "":
                # ``key = value`` split across two tokens.
                if i + 1 >= n:
                    raise ValueError(
                        f"parameter {key!r} missing value after '='"
                    )
                val = tokens[i + 1]
                i += 2
            else:
                i += 1
            out[key] = parse_value(val)
        elif i + 2 < n and tokens[i + 1] == "=":
            # ``key = value`` with three separate tokens.
            key = tok
            val = tokens[i + 2]
            out[key] = parse_value(val)
            i += 3
        else:
            raise ValueError(f"expected key=value parameter, got {tok!r}")
    return out


def _slice_subckts(
    lines: list[tuple[int, str]],
) -> tuple[list[tuple[int, str]], dict[str, _SubcktDef]]:
    """Split preprocessed lines into ``(top_lines, subckt_defs)``.

    ``subckt_defs`` is keyed by lower-cased subckt name (SPICE is
    case-insensitive for subcircuit identifiers). Nested ``.SUBCKT``
    in source is rejected; ``.ends`` may include the closing name as
    a sanity check.
    """
    top_lines: list[tuple[int, str]] = []
    subckt_defs: dict[str, _SubcktDef] = {}
    cur: Optional[_SubcktDef] = None
    for lineno, line in lines:
        parts = line.split()
        head_lc = parts[0].lower()

        if head_lc == ".subckt":
            if cur is not None:
                raise ValueError(
                    f"line {lineno}: nested .SUBCKT not supported "
                    f"(still inside {cur.name!r})"
                )
            if len(parts) < 2:
                raise ValueError(f"line {lineno}: .SUBCKT needs a name")
            sub_name = parts[1]
            # Pin names are positional tokens; ``PARAMS:`` (or the first
            # ``key=val`` token) marks the start of the optional default
            # parameter block.
            pins: list[str] = []
            param_tokens: list[str] = []
            in_params = False
            for tok in parts[2:]:
                if not in_params and (
                    tok.lower() == "params:" or "=" in tok
                ):
                    in_params = True
                if in_params:
                    param_tokens.append(tok)
                else:
                    pins.append(tok)
            try:
                defaults = _parse_param_assignments(param_tokens)
            except ValueError as e:
                raise ValueError(
                    f"line {lineno}: .SUBCKT {sub_name!r}: {e}"
                ) from None
            cur = _SubcktDef(
                name=sub_name, pins=pins, defaults=defaults, lines=[]
            )
            continue

        if head_lc == ".ends":
            if cur is None:
                raise ValueError(
                    f"line {lineno}: .ENDS without matching .SUBCKT"
                )
            if len(parts) > 1 and parts[1].lower() != cur.name.lower():
                raise ValueError(
                    f"line {lineno}: .ENDS {parts[1]!r} does not match "
                    f"open .SUBCKT {cur.name!r}"
                )
            key = cur.name.lower()
            if key in subckt_defs:
                raise ValueError(
                    f"line {lineno}: duplicate .SUBCKT {cur.name!r}"
                )
            subckt_defs[key] = cur
            cur = None
            continue

        if head_lc == ".end":
            if cur is not None:
                raise ValueError(
                    f"line {lineno}: .end inside .SUBCKT {cur.name!r}; "
                    "close the subcircuit with .ENDS first"
                )
            top_lines.append((lineno, line))
            break

        if cur is not None:
            cur.lines.append((lineno, line))
        else:
            top_lines.append((lineno, line))

    if cur is not None:
        raise ValueError(
            f".SUBCKT {cur.name!r}: missing .ENDS at end of input"
        )
    return top_lines, subckt_defs


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
) -> tuple[cas.Expr, cas.Expr | None]:
    """Parse the DC and AC values of a V/I source.

    Accepts::

        <val>                       -> (val, None)   ; applies in both modes
        DC <val>                    -> (val, 0)      ; DC bias, short at AC
        AC <val>                    -> (0,   val)
        DC <val> AC <val>           -> (dc, ac)
        AC <val> DC <val>           -> (dc, ac)
    """
    dc_val: cas.Expr | None = None
    ac_val: cas.Expr | None = None
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
        dc_val = cas.Integer(0)
    # Explicit DC keyword without AC means this is a DC-only source (AC short).
    if dc_explicit and ac_val is None:
        ac_val = cas.Integer(0)
    return dc_val, ac_val


def _require(parts: list[str], count: int, lineno: int, name: str) -> None:
    if len(parts) < count:
        raise ValueError(
            f"line {lineno}: element {name!r} needs at least {count} tokens, got {len(parts)}"
        )


def parse(text: str) -> Circuit:
    """Parse a SPICE netlist string into a :class:`Circuit`.

    Top-level lines populate the returned circuit directly. Lines
    inside ``.SUBCKT name pin1 pin2 ... .ENDS`` blocks are stored as
    reusable templates and instantiated whenever an ``X`` element
    references their name (case-insensitive). Subcircuit bodies may
    themselves reference other user subcircuits or the built-in
    ``OPAMP`` / ``TRIODE`` blocks.
    """
    lines = _preprocess(text)
    top_lines, subckt_defs = _slice_subckts(lines)
    bodies: dict[str, Circuit] = {}
    in_progress: set[str] = set()
    return _build_circuit(
        top_lines,
        subckt_defs=subckt_defs,
        bodies=bodies,
        in_progress=in_progress,
        circuit_name="circuit",
    )


def _build_circuit(
    lines: list[tuple[int, str]],
    *,
    subckt_defs: dict[str, _SubcktDef],
    bodies: dict[str, Circuit],
    in_progress: set[str],
    circuit_name: str,
) -> Circuit:
    """Parse a flat line list into a :class:`Circuit`.

    Used both for the top-level netlist and recursively for each
    ``.SUBCKT`` body the moment it is first referenced. ``bodies``
    memoizes already-built bodies — every instance of a given subckt
    name shares the same body :class:`Circuit` (each instance
    namespaces and reroutes that body independently at expand time,
    so sharing is safe and cheaper than duplicating).
    """
    circuit = Circuit(name=circuit_name)
    for lineno, line in lines:
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
        elif head == "j":
            _require(parts, 7, lineno, name)
            drain, gate, source, mtype = parts[1], parts[2], parts[3], parts[4]
            BETA = parse_value(parts[5])
            VTO = parse_value(parts[6])
            mtype_lc = mtype.lower()
            kwargs: dict[str, cas.Expr] = {}
            if len(parts) > 7:
                kwargs["LAMBDA"] = parse_value(parts[7])
            if len(parts) > 8:
                kwargs["C_gs"] = parse_value(parts[8])
            if len(parts) > 9:
                kwargs["C_gd"] = parse_value(parts[9])
            if len(parts) > 10:
                kwargs["V_GS_op"] = parse_value(parts[10])
            if len(parts) > 11:
                kwargs["V_DS_op"] = parse_value(parts[11])
            if mtype_lc == "njf":
                circuit.add_njfet(name, drain, gate, source, BETA, VTO, **kwargs)
            elif mtype_lc == "pjf":
                circuit.add_pjfet(name, drain, gate, source, BETA, VTO, **kwargs)
            else:
                raise ValueError(
                    f"line {lineno}: unknown JFET model {mtype!r}; expected NJF or PJF"
                )
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
            extra: dict[str, cas.Expr] = {}
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
                kwargs: dict[str, cas.Expr] = {}
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
                kwargs: dict[str, cas.Expr] = {}
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
                kwargs: dict[str, cas.Expr] = {}
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
            _require(parts, 3, lineno, name)
            # Split off any ``PARAMS: k=v ...`` instance overrides;
            # everything before that is the standard ``Xinst node1
            # ... nodeN subckt_name`` form (or a built-in keyword).
            head_tokens = parts[1:]
            param_tokens: list[str] = []
            for i, tok in enumerate(head_tokens):
                if tok.lower() == "params:" or "=" in tok:
                    head_tokens, param_tokens = (
                        head_tokens[:i], head_tokens[i:]
                    )
                    break
            try:
                instance_params = _parse_param_assignments(param_tokens)
            except ValueError as e:
                raise ValueError(f"line {lineno}: X{name!r}: {e}") from None

            # User-defined subckts win when the LAST positional token
            # before any PARAMS block matches a ``.SUBCKT`` name.
            # Otherwise fall back to the built-in TRIODE / OPAMP
            # dispatch keyed at parts[4].
            last_lc = head_tokens[-1].lower() if head_tokens else ""
            if last_lc in subckt_defs:
                sub_def = subckt_defs[last_lc]
                pins = head_tokens[:-1]
                if len(pins) != len(sub_def.pins):
                    raise ValueError(
                        f"line {lineno}: X{name!r} provides {len(pins)} pin "
                        f"node(s) but subckt {sub_def.name!r} declares "
                        f"{len(sub_def.pins)}: {sub_def.pins!r}"
                    )
                body = _resolve_subckt_body(
                    sub_def,
                    subckt_defs=subckt_defs,
                    bodies=bodies,
                    in_progress=in_progress,
                )
                port_map = dict(zip(sub_def.pins, pins))
                merged_params = dict(sub_def.defaults)
                merged_params.update(instance_params)
                circuit.add_subcircuit(name, body, port_map, merged_params)
                continue
            if param_tokens:
                raise ValueError(
                    f"line {lineno}: X{name!r}: PARAMS: only supported "
                    f"on user-defined .SUBCKT instances, not on built-in "
                    f"OPAMP/TRIODE dispatch"
                )
            # Restore parts to the legacy view for the built-in branch.
            parts = [name] + head_tokens

            _require(parts, 5, lineno, name)
            subckt = parts[4].upper()
            if subckt == "TRIODE":
                _require(parts, 7, lineno, name)
                plate, grid, cathode = parts[1], parts[2], parts[3]
                K_val = parse_value(parts[5])
                mu_val = parse_value(parts[6])
                kwargs: dict[str, cas.Expr] = {}
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
            elif subckt == "OPAMP":
                # Xxxx IN+ IN- OUT OPAMP [A]
                in_p, in_n, out = parts[1], parts[2], parts[3]
                A_val = parse_value(parts[5]) if len(parts) > 5 else None
                circuit.add_opamp(name, in_p, in_n, out, A_val)
            else:
                raise ValueError(
                    f"line {lineno}: unknown subcircuit type {subckt!r} for "
                    f"{name!r} (no .SUBCKT defined and not a built-in)"
                )
        elif head == "k":
            # Mutual coupling: Kname L1 L2 ... k
            # Last token is always the coupling coefficient.
            _require(parts, 4, lineno, name)
            ind_names: list[str] = parts[1:-1]
            k_val = parse_value(parts[-1])
            circuit.add_mutual_coupling(name, ind_names, k_val)
        else:
            raise ValueError(f"line {lineno}: unsupported element {name!r}")

    return circuit


def _resolve_subckt_body(
    sub_def: _SubcktDef,
    *,
    subckt_defs: dict[str, _SubcktDef],
    bodies: dict[str, Circuit],
    in_progress: set[str],
) -> Circuit:
    """Return the body :class:`Circuit` for a referenced subckt.

    Memoized in ``bodies``: every ``X`` reference to the same subckt
    name shares the same body object. Detects circular references
    (subckt A → B → A) via an in-progress guard.
    """
    key = sub_def.name.lower()
    if key in bodies:
        return bodies[key]
    if key in in_progress:
        raise ValueError(
            f".SUBCKT {sub_def.name!r}: circular subcircuit reference"
        )
    in_progress.add(key)
    try:
        body = _build_circuit(
            sub_def.lines,
            subckt_defs=subckt_defs,
            bodies=bodies,
            in_progress=in_progress,
            circuit_name=sub_def.name,
        )
    finally:
        in_progress.discard(key)
    bodies[key] = body
    return body


def parse_file(path: str | Path) -> Circuit:
    """Parse a SPICE netlist from a file path."""
    return parse(Path(path).read_text())


# ---------------------------------------------------------------------------
# SPICE writer
# ---------------------------------------------------------------------------


def _format_value(v: object) -> str:
    """Render a component value as a SPICE token.

    Numeric expressions become decimal literals; symbolic expressions
    fall back to ``str(expr)``. Compound symbolic expressions (anything
    containing whitespace) cannot round-trip through :func:`parse_value`
    — emitted as-is so the caller can review.
    """
    if isinstance(v, str):
        return v
    expr = cas.sympify(v)
    if expr.is_number:
        try:
            f = float(expr)
        except (TypeError, ValueError):
            return str(expr)
        if f == int(f) and abs(f) < 1e16:
            return str(int(f))
        return f"{f:.12g}"
    return str(expr)


def _format_node(node: str) -> str:
    """SPICE doesn't allow whitespace in node names; pass-through otherwise."""
    if any(ch.isspace() for ch in node):
        raise ValueError(f"node name {node!r} contains whitespace")
    return node


def _stable_subckt_name(
    body: "Circuit", cls_name: str, used: set[str]
) -> str:
    """Pick a unique ``.SUBCKT`` identifier for ``body``.

    Prefers ``body.name`` when it's set to something other than the
    generic default (``"circuit"``); falls back to the SubCircuit
    subclass name. Disambiguates collisions with a numeric suffix.
    """
    raw = body.name if body.name and body.name != "circuit" else cls_name
    base = re.sub(r"[^A-Za-z0-9_]", "_", raw) or cls_name
    candidate = base
    i = 2
    while candidate.lower() in used:
        candidate = f"{base}_{i}"
        i += 1
    used.add(candidate.lower())
    return candidate


def _collect_templates(
    circuit: "Circuit",
) -> dict[int, tuple[str, "Circuit", list[str]]]:
    """Walk the hierarchy and assign one ``.SUBCKT`` template per body.

    Returns ``{id(body): (subckt_name, body, pin_order)}``. Bodies are
    keyed by Python identity so multiple ``X`` instances that share a
    body (the common SPICE-parsed case) collapse to one ``.SUBCKT``
    block. Built-in OPAMP / OPAMP1 / Triode wrappers — which are emitted
    via the legacy ``X ... OPAMP`` form — are skipped here.
    """
    from sycan.components.blocks.subcircuit import SubCircuit
    from sycan.components.blocks.opamp import OPAMP, OPAMP1

    templates: dict[int, tuple[str, "Circuit", list[str]]] = {}
    used_names: set[str] = set()

    def visit(comp_list: list) -> None:
        for c in comp_list:
            if not isinstance(c, SubCircuit):
                continue
            if isinstance(c, (OPAMP, OPAMP1)):
                # Emitted inline via the built-in X-form; no template.
                continue
            key = id(c.body)
            if key not in templates:
                pins = list(c.port_map.keys())
                name = _stable_subckt_name(c.body, type(c).__name__, used_names)
                templates[key] = (name, c.body, pins)
            visit(c.body.components)

    visit(circuit.components)
    return templates


def _emit_subckt_block(
    name: str,
    body: "Circuit",
    pins: list[str],
    templates: dict[int, tuple[str, "Circuit", list[str]]],
) -> list[str]:
    """Render one ``.SUBCKT name pins ... .ENDS name`` block."""
    header = ".SUBCKT " + " ".join([name, *pins])
    lines = [header]
    for comp in body.components:
        lines.extend(_emit_component(comp, templates))
    lines.append(f".ENDS {name}")
    return lines


def _emit_component(
    comp,
    templates: dict[int, tuple[str, "Circuit", list[str]]],
) -> list[str]:
    """Render a single component as one or more SPICE lines.

    Dispatches on the concrete class name. Unknown classes raise
    :class:`NotImplementedError` rather than producing silently-broken
    output.
    """
    from sycan.components.blocks.subcircuit import SubCircuit
    from sycan.components.blocks.opamp import OPAMP, OPAMP1
    from sycan.components.active.triode import Triode

    cls = type(comp).__name__

    # SubCircuit (and the OPAMP/Triode special cases) come first because
    # they're the polymorphic catch-all for hierarchy.
    if isinstance(comp, OPAMP):
        # Xname in_p in_n out OPAMP A
        pm = comp.port_map
        return [
            " ".join([
                comp.name, pm["in_p"], pm["in_n"], pm["out"],
                "OPAMP", _format_value(comp.A),
            ])
        ]
    if isinstance(comp, OPAMP1):
        # OPAMP1 has no built-in SPICE letter form; emit as a generic
        # subcircuit if a template exists, otherwise refuse.
        raise NotImplementedError(
            f"OPAMP1 {comp.name!r}: SPICE output not implemented "
            "(use OPAMP for the ideal model or wire the first-order "
            "block manually with R/C/E elements)"
        )
    if isinstance(comp, SubCircuit):
        key = id(comp.body)
        sub_name, _body, pins = templates[key]
        nodes = [comp.port_map[p] for p in pins]
        tokens = [comp.name, *nodes, sub_name]
        if comp.params:
            tokens.append("PARAMS:")
            for k, v in comp.params.items():
                tokens.append(f"{k}={_format_value(v)}")
        return [" ".join(tokens)]

    if cls in ("Resistor", "Capacitor", "Inductor"):
        return [_emit_two_term(comp)]
    if cls in ("VoltageSource", "CurrentSource"):
        return [_emit_v_or_i_source(comp)]
    if cls in ("VCVS", "VCCS"):
        return [_emit_dep_voltage(comp)]
    if cls in ("CCCS", "CCVS"):
        return [_emit_dep_current(comp)]
    if cls == "Diode":
        return [_emit_diode(comp)]
    if cls == "BJT":
        return [_emit_bjt(comp)]
    if cls in ("NMOS_L1", "PMOS_L1", "NMOS_3T", "PMOS_3T",
               "NMOS_4T", "PMOS_4T",
               "NMOS_subthreshold", "PMOS_subthreshold"):
        return [_emit_mosfet(comp, cls)]
    if cls in ("NJFET", "PJFET"):
        return [_emit_jfet(comp, cls)]
    if cls == "TLINE":
        return [_emit_tline(comp)]
    if cls == "Port":
        return [_emit_port(comp)]
    if cls == "GND":
        return [f"{comp.name} {_format_node(comp.node)}"]
    if cls == "MutualCoupling":
        return [
            " ".join([
                comp.name,
                *[str(n) for n in comp._inductor_names],
                _format_value(comp.k),
            ])
        ]
    if isinstance(comp, Triode):
        return [
            " ".join([
                comp.name,
                _format_node(comp.plate),
                _format_node(comp.grid),
                _format_node(comp.cathode),
                "TRIODE",
                _format_value(comp.K),
                _format_value(comp.mu),
            ])
        ]

    raise NotImplementedError(
        f"to_spice: no SPICE-letter emitter for {cls!r} "
        f"(component {getattr(comp, 'name', '?')!r})"
    )


def _emit_two_term(comp) -> str:
    return " ".join([
        comp.name,
        _format_node(comp.n_plus),
        _format_node(comp.n_minus),
        _format_value(comp.value),
    ])


def _emit_v_or_i_source(comp) -> str:
    """Emit V/I sources as ``DC <val> [AC <val>]`` (waveforms not supported)."""
    if comp.waveform is not None:
        raise NotImplementedError(
            f"to_spice: waveform sources are not yet supported "
            f"({comp.name!r}, waveform={comp.waveform!r})"
        )
    tokens = [
        comp.name,
        _format_node(comp.n_plus),
        _format_node(comp.n_minus),
    ]
    if comp.ac_value is not None:
        tokens += ["DC", _format_value(comp.value),
                   "AC", _format_value(comp.ac_value)]
    else:
        tokens.append(_format_value(comp.value))
    return " ".join(tokens)


def _emit_dep_voltage(comp) -> str:
    """E (VCVS) and G (VCCS) — voltage-controlled."""
    return " ".join([
        comp.name,
        _format_node(comp.n_plus),
        _format_node(comp.n_minus),
        _format_node(comp.nc_plus),
        _format_node(comp.nc_minus),
        _format_value(comp.gain),
    ])


def _emit_dep_current(comp) -> str:
    """F (CCCS) and H (CCVS) — current-controlled by another V source."""
    return " ".join([
        comp.name,
        _format_node(comp.n_plus),
        _format_node(comp.n_minus),
        comp.ctrl,
        _format_value(comp.gain),
    ])


def _emit_diode(comp) -> str:
    tokens = [
        comp.name,
        _format_node(comp.anode),
        _format_node(comp.cathode),
        _format_value(comp.IS),
    ]
    if getattr(comp, "N", None) is not None:
        tokens.append(_format_value(comp.N))
    if getattr(comp, "V_T", None) is not None:
        tokens.append(_format_value(comp.V_T))
    return " ".join(tokens)


def _emit_bjt(comp) -> str:
    return " ".join([
        comp.name,
        _format_node(comp.collector),
        _format_node(comp.base),
        _format_node(comp.emitter),
        comp.polarity,
        _format_value(comp.IS),
        _format_value(comp.BF),
        _format_value(comp.BR),
    ])


def _emit_mosfet(comp, cls: str) -> str:
    """All MOSFET flavours map to ``Mname ... TYPE mu Cox W L V_TH ...``."""
    tokens = [comp.name, _format_node(comp.drain), _format_node(comp.gate),
              _format_node(comp.source)]
    if cls in ("NMOS_4T", "PMOS_4T"):
        tokens.append(_format_node(comp.bulk))
        type_attr = cls.upper()
    else:
        type_attr = cls.upper()
    tokens.append(type_attr)
    tokens += [
        _format_value(comp.mu_n),
        _format_value(comp.Cox),
        _format_value(comp.W),
        _format_value(comp.L),
        _format_value(getattr(comp, "V_TH0", None) or comp.V_TH),
    ]
    return " ".join(tokens)


def _emit_jfet(comp, cls: str) -> str:
    return " ".join([
        comp.name,
        _format_node(comp.drain),
        _format_node(comp.gate),
        _format_node(comp.source),
        "NJF" if cls == "NJFET" else "PJF",
        _format_value(comp.BETA),
        _format_value(comp.VTO),
    ])


def _emit_tline(comp) -> str:
    return " ".join([
        comp.name,
        _format_node(comp.n_in_p),
        _format_node(comp.n_in_m),
        _format_node(comp.n_out_p),
        _format_node(comp.n_out_m),
        _format_value(comp.Z0),
        _format_value(comp.td),
    ])


def _emit_port(comp) -> str:
    tokens = [comp.name, _format_node(comp.n_plus), _format_node(comp.n_minus)]
    if getattr(comp, "role", "generic") != "generic":
        tokens.append(comp.role)
    return " ".join(tokens)


def to_spice(circuit: "Circuit", *, title: Optional[str] = None) -> str:
    """Serialize ``circuit`` to a SPICE netlist string.

    Hierarchical designs are emitted with one ``.SUBCKT`` block per
    distinct body identity, followed by the top-level component list.
    SubCircuit instances become ``X`` references that carry their
    ``params`` dict via ``PARAMS:``. The first line is a SPICE title
    (defaults to ``circuit.name``).

    Components without a SPICE-letter representation (e.g. behavioural
    sources, switches, varactors, transfer-function and signal-flow
    blocks, ``OPAMP1``) raise :class:`NotImplementedError`.
    """
    title_line = title if title is not None else circuit.name
    lines: list[str] = [title_line]

    templates = _collect_templates(circuit)
    for name, body, pins in templates.values():
        lines.append("")
        lines.extend(_emit_subckt_block(name, body, pins, templates))

    if templates:
        lines.append("")

    for comp in circuit.components:
        lines.extend(_emit_component(comp, templates))

    lines.append(".end")
    return "\n".join(lines) + "\n"


def write_file(circuit: "Circuit", path: str | Path) -> Path:
    """Write ``circuit`` to ``path`` as a SPICE netlist."""
    p = Path(path)
    p.write_text(to_spice(circuit))
    return p
