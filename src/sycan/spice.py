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

        <val>                       -> (val, None)
        DC <val>                    -> (val, None)
        AC <val>                    -> (0,   val)
        DC <val> AC <val>           -> (dc, ac)
        AC <val> DC <val>           -> (dc, ac)
    """
    dc_val: sp.Expr | None = None
    ac_val: sp.Expr | None = None
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
        else:
            raise ValueError(f"line {lineno}: unsupported element {name!r}")

    return circuit


def parse_file(path: str | Path) -> Circuit:
    """Parse a SPICE netlist from a file path."""
    return parse(Path(path).read_text())
