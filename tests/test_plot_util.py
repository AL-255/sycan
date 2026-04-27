"""Tests for the engineering-notation formatter ``sycan.plot_util.fmt``."""
import pytest

from sycan.plot_util import fmt


@pytest.mark.parametrize("value,unit,expected", [
    # Nominal scales: every SI decade picks the right prefix and the
    # mantissa lands in [1, 1000).
    (0.9,        "V",  "900 mV"),
    (4.05e-5,    "A",  "40.5 µA"),
    (2.5e9,      "Hz", "2.50 GHz"),
    (1.234e-12,  "F",  "1.23 pF"),
    (1.0e3,      "Ω",  "1.00 kΩ"),
    (1.0,        "V",  "1.00 V"),
    (5e-15,      "C",  "5.00 fC"),
    (3e-10,      "s",  "300 ps"),
    # Negative values keep the leading minus.
    (-1.5,       "V",  "-1.50 V"),
    # No unit: just a scaled number with prefix.
    (1.5e3,      "",   "1.50 k"),
    # Dimensionless and small enough that no prefix is picked.
    (1.5,        "",   "1.50"),
])
def test_fmt_typical(value, unit, expected):
    assert fmt(value, unit) == expected


def test_fmt_zero():
    """Zero is special-cased — no exponent, no prefix, optional unit dangles."""
    assert fmt(0.0, "V") == "0 V"
    assert fmt(0,   "")  == "0"


def test_fmt_sign_flag():
    """``sign=True`` emits a leading plus on positives, leaves negatives alone."""
    assert fmt(1.234,  "V", sign=True) == "+1.23 V"
    assert fmt(-1.234, "V", sign=True) == "-1.23 V"
    assert fmt(0,      "V", sign=True) == "+0 V"


def test_fmt_places():
    """``places`` controls significant digits (not decimal places)."""
    # places=2: 2 sig digits. 1.234 V -> "1.2 V" (not "1.23").
    assert fmt(1.234, "V", places=2) == "1.2 V"
    # places=4: 1.234 V -> "1.234 V".
    assert fmt(1.234, "V", places=4) == "1.234 V"
    # places=1 with a 3-digit mantissa drops to integer.
    assert fmt(1.234e-3, "V", places=1) == "1 mV"


def test_fmt_boundary_promotion():
    """A mantissa that rounds up to 1000 promotes to the next prefix."""
    # 999.5 µA at 3 sig digits would round the mantissa to "1000",
    # so we should bump µ → m and emit "1.00 mA".
    assert fmt(999.5e-6, "A") == "1.00 mA"
    # Same near 1.0 V (very close to the kilo-boundary in mV).
    assert fmt(0.9995, "V") == "1.00 V"


def test_fmt_extreme_values_clamp_to_si_range():
    """Beyond yotta / yocto, the formatter clamps to the largest prefix.

    No exponent symbol fallback today — matching matplotlib EngFormatter
    is enough; we just stop scaling at the table edge.
    """
    huge = 1e30
    s = fmt(huge, "Hz")
    # Mantissa is no longer in [1, 1000) for clamped values — that's
    # the price of staying inside the SI prefix table.
    assert s.endswith("YHz")


def test_fmt_nan_inf():
    """NaN and ±inf pass through verbatim with the unit appended."""
    assert fmt(float("nan"), "V") == "nan V"
    assert fmt(float("inf"), "V") == "inf V"
    assert fmt(-float("inf"), "V") == "-inf V"


def test_fmt_separator():
    """``sep`` controls the gap between mantissa and prefix+unit."""
    assert fmt(1e-3, "V", sep="") == "1.00mV"
    assert fmt(1e-3, "V", sep=" ") == "1.00 mV"  # narrow no-break space
