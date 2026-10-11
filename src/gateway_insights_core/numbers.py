# SPDX-License-Identifier: Apache-2.0
"""Numbers: everything money-like is Decimal, never float.

The console sends amounts and rates as JSON numbers or numeric strings — a
Java BigDecimal may print in scientific notation ("0E-8") — and some widgets
send display strings ("1 234 567.89", "97.25%"). All of them go
through to_decimal; formatting for output is here too, so every tool prints a
value the same way.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

# The console's display format: '.' decimal separator, a space (or a no-break /
# thin space) between thousands, an optional trailing '%'.
_DISPLAY_RE = re.compile(r"^-?[\d\s\u00a0\u202f]+(\.\d+)?%?$")
# A decimal as a Java BigDecimal prints it: plain or scientific ("0E-8", "1.5E+3").
_PLAIN_RE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")


def to_decimal(value: Any) -> Optional[Decimal]:
    """A Decimal, or None for an absent value. A non-numeric string is refused, not guessed."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(repr(value))
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if _PLAIN_RE.match(text):
            return Decimal(text)
        if not _DISPLAY_RE.match(text):
            raise ValueError(f"not a number: {value!r}")
        text = re.sub(r"[\s\u00a0\u202f]", "", text)
        percent = text.endswith("%")
        text = text.rstrip("%")
        try:
            number = Decimal(text)
        except InvalidOperation:
            raise ValueError(f"not a number: {value!r}") from None
        return number / 100 if percent else number
    raise ValueError(f"not a number: {value!r}")


def ratio(numerator: Optional[Decimal], denominator: Optional[Decimal]) -> Optional[Decimal]:
    """numerator / denominator, or None when either is absent or the denominator is zero."""
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def _group(digits: str) -> str:
    out = []
    while len(digits) > 3:
        out.insert(0, digits[-3:])
        digits = digits[:-3]
    out.insert(0, digits)
    return ",".join(out)


def fmt_number(value: Optional[Decimal], places: int = 2) -> str:
    if value is None:
        return "—"
    quantised = value.quantize(Decimal(1).scaleb(-places)) if places else value.quantize(Decimal(1))
    sign = "-" if quantised < 0 else ""
    text = f"{abs(quantised):f}"
    whole, _, frac = text.partition(".")
    return f"{sign}{_group(whole)}" + (f".{frac}" if frac else "")


def fmt_money(value: Optional[Decimal], currency: str = "") -> str:
    text = fmt_number(value, 2)
    return f"{text} {currency}".rstrip() if value is not None else text


def fmt_count(value: Optional[Decimal]) -> str:
    return fmt_number(value, 0)


def fmt_rate(fraction: Optional[Decimal]) -> str:
    """A fraction (0.9725) as a percentage (97.25%)."""
    return "—" if fraction is None else f"{fmt_number(fraction * 100, 2)}%"


def fmt_pp(fraction: Optional[Decimal]) -> str:
    """A difference of two fractions as percentage points, signed (+1.23 pp)."""
    if fraction is None:
        return "—"
    text = fmt_number(fraction * 100, 2)
    return f"{'+' if fraction > 0 else ''}{text} pp"


def fmt_signed(value: Optional[Decimal], places: int = 2) -> str:
    if value is None:
        return "—"
    text = fmt_number(value, places)
    return f"+{text}" if value > 0 else text
