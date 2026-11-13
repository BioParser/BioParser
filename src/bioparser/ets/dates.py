"""Parse a printed date into one ETS calendar day."""

import re
from datetime import datetime

# Year-first numbers, then a written month. A numeric day and month is handled
# separately, and only when one number is greater than 12.
_DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y.%m.%d",
    "%d %b %Y",
    "%d %B %Y",
    "%d %b, %Y",
    "%d %B, %Y",
    "%d. %b %Y",
    "%d. %B %Y",
    "%d. %b, %Y",
    "%d. %B, %Y",
    "%d-%b-%Y",
    "%d-%B-%Y",
    "%d/%b/%Y",
    "%d/%B/%Y",
    "%Y %b %d",
    "%Y %B %d",
    "%Y-%b-%d",
    "%Y-%B-%d",
    "%b %d, %Y",
    "%B %d, %Y",
    "%b %d %Y",
    "%B %d %Y",
)
_NUMERIC_DAY_MONTH = re.compile(r"^(\d{1,2})([./-])(\d{1,2})\2(\d{4})$")
_ORDINAL = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)\b", re.IGNORECASE)
_LEADING_THE = re.compile(r"^the\s+", re.IGNORECASE)
_OF = re.compile(r"\b(\d{1,2})\s+of\s+", re.IGNORECASE)
_SEPT = re.compile(r"\bSept\.?(?!\w)", re.IGNORECASE)
_ABBREV_PERIOD = re.compile(
    r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.(?!\w)",
    re.IGNORECASE,
)
_PERIOD_BEFORE_YEAR = re.compile(r"([A-Za-z])\s+(\d{1,2})\.(?=\s*\d{4}\b)")
_DASHES = str.maketrans({"\u2013": "-", "\u2014": "-"})


def parse_date(text: str) -> str | None:
    """Calendar day as ``YYYY-MM-DD``, or None when the text is not one.

    A numeric day and month is accepted when one number is greater than 12,
    because that number is the day. ``08/03/1963`` is left empty.
    """
    prepared = _prepare(text)
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(prepared, fmt).date().isoformat()
        except ValueError:
            continue

    # One number greater than 12 is the day. Both at most 12 could be either order.
    match = _NUMERIC_DAY_MONTH.fullmatch(prepared)
    if match is None:
        return None
    first = int(match.group(1))
    second = int(match.group(3))
    year = int(match.group(4))
    if first > 12 and second <= 12:
        day, month = first, second
    elif second > 12 and first <= 12:
        month, day = first, second
    else:
        return None
    try:
        return datetime(year, month, day).date().isoformat()
    except ValueError:
        return None


def _prepare(text: str) -> str:
    """Turn printed punctuation and prose into a form strptime accepts."""
    text = text.strip().translate(_DASHES)
    text = _ORDINAL.sub(r"\1", text)
    text = _LEADING_THE.sub("", text)
    text = _OF.sub(r"\1 ", text)
    text = _SEPT.sub("Sep", text)
    text = _ABBREV_PERIOD.sub(r"\1", text)
    return _PERIOD_BEFORE_YEAR.sub(r"\1 \2", text)
