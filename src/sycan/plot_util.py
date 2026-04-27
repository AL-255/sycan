"""Plot / readout helpers.

Lives separately from the plot-emitting modules (``svg_util.bode_svg``,
the autodraw labels, the REPL examples) so anything that needs to
*print* a number nicely can pull from one place. The heavy lifter is
:func:`fmt`, an engineering-notation formatter modelled on
``matplotlib.ticker.EngFormatter`` but written in plain Python so the
package never grows a matplotlib runtime dependency just to label
microamps and megahertz consistently.
"""
from __future__ import annotations

import math


# SI prefixes keyed by power-of-10 exponent (must be a multiple of 3).
# Range matches matplotlib.ticker.EngFormatter — the SI 1991-vintage
# 17-prefix set, from ``yocto`` (1e-24) to ``yotta`` (1e+24).
_SI_PREFIXES = {
    -24: "y", -21: "z", -18: "a", -15: "f", -12: "p", -9: "n",
    -6: "µ",  -3: "m",   0: "",    3: "k",    6: "M",   9: "G",
    12: "T",  15: "P",  18: "E",  21: "Z",   24: "Y",
}
_MIN_EXP = min(_SI_PREFIXES)
_MAX_EXP = max(_SI_PREFIXES)


def fmt(
    value,
    unit: str = "",
    *,
    places: int = 3,
    sep: str = " ",
    sign: bool = False,
) -> str:
    """Engineering-notation formatter, matplotlib ``EngFormatter``-style.

    Scales ``value`` so the mantissa sits in ``[1, 1000)`` and emits
    ``<mantissa><sep><SI prefix><unit>``. ``places`` is the number of
    *significant digits* in the mantissa — picked over matplotlib's
    "decimal places after the point" because that's what bias-point /
    measurement readouts actually want (``900 mV`` instead of
    ``0.900 V`` regardless of how big the original number was).

    Parameters
    ----------
    value:
        Number to format. ``int`` / ``float`` / anything ``float()``
        accepts. NaN and ±inf pass through verbatim.
    unit:
        Unit suffix appended after the SI prefix (``"V"``, ``"Hz"``,
        ``"Ω"``, …). Default ``""``.
    places:
        Significant digits in the mantissa. Default ``3``.
    sep:
        Separator between the mantissa and the prefix+unit.
        Default ``" "``.
    sign:
        If True, positive values get a leading ``"+"``. Default ``False``.

    Examples
    --------
    >>> fmt(0.9, "V")
    '900 mV'
    >>> fmt(4.05e-5, "A")
    '40.5 µA'
    >>> fmt(2.5e9, "Hz")
    '2.50 GHz'
    >>> fmt(0, "V")
    '0 V'
    >>> fmt(-1.234, "V", sign=True)
    '-1.23 V'
    >>> fmt(1.5, "")
    '1.50'
    """
    try:
        v = float(value)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"fmt: cannot format {value!r} as a number") from exc

    if math.isnan(v):
        body = "nan"
        suffix = unit
    elif math.isinf(v):
        body = ("-" if v < 0 else ("+" if sign else "")) + "inf"
        suffix = unit
    elif v == 0:
        # 0 has no defined exponent. Don't pretend one — emit it
        # naked, with whatever unit the caller asked for.
        body = ("+" if sign else "") + "0"
        suffix = unit
    else:
        s = "-" if v < 0 else ("+" if sign else "")
        av = abs(v)
        exp10 = int(math.floor(math.log10(av)))
        # Round down to the nearest multiple of 3 — that's the
        # engineering-notation rule, and it lines up with the SI
        # prefix table.
        eng_exp = (exp10 // 3) * 3
        if eng_exp < _MIN_EXP:
            eng_exp = _MIN_EXP
        elif eng_exp > _MAX_EXP:
            eng_exp = _MAX_EXP
        mant = av / (10.0 ** eng_exp)
        # ``leading`` counts the digits before the decimal point of the
        # mantissa (1 for [1, 10), 2 for [10, 100), 3 for [100, 1000)).
        leading = int(math.floor(math.log10(mant))) + 1
        decimals = max(0, places - leading)
        mant_str = f"{mant:.{decimals}f}"
        # Fixed-format rounding can push the mantissa to "1000" right
        # at the boundary (e.g. 999.6 with places=3 -> "1000"). Promote
        # to the next prefix and re-format so we stay in [1, 1000).
        # Use the *rounded* mantissa (1000 / 1000 == 1.0), not the
        # unrounded one — otherwise 999.5 µA would render as "1.000 mA"
        # instead of the expected "1.00 mA".
        if float(mant_str) >= 1000.0 and eng_exp + 3 <= _MAX_EXP:
            eng_exp += 3
            leading = 1
            decimals = max(0, places - leading)
            mant_str = f"{1.0:.{decimals}f}"
        body = s + mant_str
        suffix = _SI_PREFIXES[eng_exp] + unit

    if suffix:
        return f"{body}{sep}{suffix}"
    return body


__all__ = ["fmt"]
