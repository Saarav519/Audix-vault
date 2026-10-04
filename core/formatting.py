"""Display formatting: Indian digit grouping, ₹, compact L/Cr, dates.

Pure Python. No Django imports, so it can be used from core.calc and exports.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

MINUS = "−"  # true minus sign
RUPEE = "₹"


def to_decimal(value) -> Decimal:
    if value is None or value == "":
        return Decimal(0)
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal(0)


def round_dec(value, places: int = 0) -> Decimal:
    q = Decimal(1).scaleb(-places)
    return to_decimal(value).quantize(q, rounding=ROUND_HALF_UP)


def indian_group(digits: str) -> str:
    """Group a string of digits the Indian way: 1,23,45,678."""
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ",".join(groups + [tail])


def num(value, decimals: int = 0) -> str:
    """Number with Indian grouping and a true minus sign."""
    v = round_dec(value, decimals)
    sign = MINUS if v < 0 else ""
    v = abs(v)
    text = f"{v:.{decimals}f}"
    if "." in text:
        whole, frac = text.split(".")
        return f"{sign}{indian_group(whole)}.{frac}"
    return f"{sign}{indian_group(text)}"


def qty(value, signed: bool = False) -> str:
    """Quantity: whole numbers without decimals, otherwise up to 3 decimals. `signed` adds "+" to an excess."""
    v = round_dec(value, 3)
    if v == v.to_integral_value():
        text = num(v, 0)
    else:
        text = num(v, 3).rstrip("0").rstrip(".")
    return f"+{text}" if signed and v > 0 else text


def units(value, signed: bool = False) -> str:
    """Quantity with its unit word: "1 unit", "2 units", "−1 unit", "+3 units"."""
    v = round_dec(value, 3)
    return f"{qty(v, signed)} {'unit' if abs(v) == 1 else 'units'}"


_ONE_UNITS = re.compile(r"(?<![\d.,])([+\-−]?1) units\b")


def fix_unit_plural(text: str) -> str:
    """'1 units' -> '1 unit' in text built from editable templates."""
    return _ONE_UNITS.sub(r"\1 unit", text)


def inr(value, decimals: int = 0, signed: bool = False) -> str:
    """₹ amount with Indian grouping. Negative values get "−₹"."""
    v = round_dec(value, decimals)
    if v < 0:
        sign = MINUS
    elif signed and v > 0:
        sign = "+"
    else:
        sign = ""
    return f"{sign}{RUPEE}{num(abs(v), decimals)}"


def compact(value) -> str:
    """1,23,45,678 -> "1.23 Cr", 2,50,000 -> "2.50 L", smaller values grouped."""
    v = to_decimal(value)
    sign = MINUS if v < 0 else ""
    a = abs(v)
    if a >= Decimal("1e7"):
        return f"{sign}{round_dec(a / Decimal('1e7'), 2)} Cr"
    if a >= Decimal("1e5"):
        return f"{sign}{round_dec(a / Decimal('1e5'), 2)} L"
    return f"{sign}{num(a, 0)}"


def compact_inr(value, signed: bool = False) -> str:
    v = to_decimal(value)
    if v < 0:
        sign = MINUS
    elif signed and v > 0:
        sign = "+"
    else:
        sign = ""
    return f"{sign}{RUPEE}{compact(abs(v))}"


def pct(value, decimals: int = 2, signed: bool = False) -> str:
    v = round_dec(value, decimals)
    if v < 0:
        sign = MINUS
    elif signed and v > 0:
        sign = "+"
    else:
        sign = ""
    return f"{sign}{abs(v):.{decimals}f}%"


def plain_pct(value) -> str:
    """Threshold style: 2 -> "2%", 1.5 -> "1.5%"."""
    v = to_decimal(value).normalize()
    text = f"{v:f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return f"{text}%"


def fmt_date(d) -> str:
    if d is None:
        return ""
    if isinstance(d, datetime):
        d = d.date()
    return f"{d.day} {d.strftime('%b %Y')}"


def short_date(d: date) -> str:
    return f"{d.day} {d.strftime('%b')}"


def plural(n, word: str, plural_word: str | None = None) -> str:
    n_int = int(n)
    if n_int == 1:
        return f"1 {word}"
    return f"{num(n_int)} {plural_word or word + 's'}"


def file_size(n_bytes) -> str:
    n = float(n_bytes or 0)
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"
