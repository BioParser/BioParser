"""Parse a printed ETS number. Absent-field defaults are the caller's choice."""

import re
from decimal import Decimal

# No exponent and no leading zero. "." is the only decimal separator.
_NON_NEGATIVE = re.compile(r"^(?:0|[1-9]\d*)(?:\.\d+)?$|^\.\d+$")
_SIGNED = re.compile(r"^[+-]?(?:0|[1-9]\d*)(?:\.\d+)?$|^[+-]?\.\d+$")
_COUNT = re.compile(r"^[1-9]\d*$")
_INTEGER = re.compile(r"^[+-]?(?:0|[1-9]\d*)$")


def canonical_decimal(text: str) -> str | None:
    """Positive decimal without an exponent, or None when the text is not one.

    Trailing zeros after the decimal point are removed.
    """
    if _NON_NEGATIVE.fullmatch(text) is None:
        return None
    number = Decimal(text)
    if not number.is_finite() or number <= 0:
        return None
    rendered = format(number, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def parse_integer(text: str) -> int | None:
    """Integer without a leading zero, or None when the text is not one."""
    if _INTEGER.fullmatch(text) is None:
        return None
    return int(text)


def parse_count(text: str) -> int | None:
    """Integer greater than or equal to 1, or None when the text is not one."""
    if _COUNT.fullmatch(text) is None:
        return None
    return int(text)


def parse_decimal(text: str) -> float | None:
    """Non-negative decimal, or None when the text is not one."""
    return _decimal(text, signed=False)


def parse_signed_decimal(text: str) -> float | None:
    """Signed decimal, or None when the text is not one."""
    return _decimal(text, signed=True)


def _decimal(text: str, *, signed: bool) -> float | None:
    pattern = _SIGNED if signed else _NON_NEGATIVE
    if pattern.fullmatch(text) is None:
        return None
    number = Decimal(text)
    if not number.is_finite():
        return None
    return float(number)
