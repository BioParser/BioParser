"""Spelling-insensitive keys for printed units and printed levels.

A paper prints ``Kg``, ``mm.``, ``km²``, or ``individuals ha-1``. The list names each unit
once, and the key below makes those spellings meet. The key is only for matching. The row
keeps the printed text in its verbatim column and the list's own spelling in ``traitUnit``.
"""

import re
import unicodedata

# Applied to each side of a "/" after the unit is folded. Every value is the list's spelling,
# folded the same way. A name that is not here stands for itself.
_UNIT_ALIASES: dict[str, str] = {
    "d": "day",
    "days": "day",
    "wk": "week",
    "wks": "week",
    "weeks": "week",
    "mo": "month",
    "mos": "month",
    "months": "month",
    "yr": "year",
    "yrs": "year",
    "years": "year",
    "sec": "s",
    "secs": "s",
    "second": "s",
    "seconds": "s",
    "msec": "ms",
    "millisecond": "ms",
    "milliseconds": "ms",
    "gram": "g",
    "grams": "g",
    "kilogram": "kg",
    "kilograms": "kg",
    "milligram": "mg",
    "milligrams": "mg",
    "millimeter": "mm",
    "millimeters": "mm",
    "millimetre": "mm",
    "millimetres": "mm",
    "centimeter": "cm",
    "centimeters": "cm",
    "centimetre": "cm",
    "centimetres": "cm",
    "meter": "m",
    "meters": "m",
    "metre": "m",
    "metres": "m",
    "kilometer": "km",
    "kilometers": "km",
    "kilometre": "km",
    "kilometres": "km",
    "hectare": "ha",
    "hectares": "ha",
    "animal": "individuals",
    "animals": "individuals",
    "individual": "individuals",
    "ind": "individuals",
}

# "individuals ha-1" and "individuals km-2" mean "per hectare" and "per square kilometre".
_NEGATIVE_EXPONENT = re.compile(r"^(?P<top>.+?)[\s·]+(?P<unit>[a-z]+)-(?P<power>[12])$")
_SEPARATORS = re.compile(r"[\s\-_/]+")

# A printed form and the level it stands for. It only applies to a trait that has that level,
# so `Yes` for a trait with the levels hibernation, torpor, and neither matches nothing.
_LEVEL_ALIASES: dict[str, str] = {
    "y": "yes",
    "true": "yes",
    "1": "yes",
    "n": "no",
    "false": "no",
    "0": "no",
}


def normalize_unit(text: str) -> str:
    """A key that is the same for every common spelling of one unit.

    Case, spaces, a trailing period, a superscript or ``^`` exponent, a per-unit written with
    a negative exponent, and a plural or a spelled-out name all fold to one key.
    """
    folded = unicodedata.normalize("NFKC", text).casefold().strip().replace("^", "")
    match = _NEGATIVE_EXPONENT.match(folded)
    if match is not None:
        power = "" if match["power"] == "1" else match["power"]
        folded = f"{match['top']}/{match['unit']}{power}"
    folded = re.sub(r"\s+", "", folded).rstrip(".")
    return "/".join(_UNIT_ALIASES.get(part, part) for part in folded.split("/"))


def normalize_level(text: str) -> str:
    """A key that ignores case and the separators between the words of a level."""
    return _SEPARATORS.sub("", unicodedata.normalize("NFKC", text).casefold())


def level_keys(text: str) -> tuple[str, ...]:
    """The keys a printed level may match: its own, and the level it is a common form of."""
    key = normalize_level(text)
    alias = _LEVEL_ALIASES.get(key)
    return (key,) if alias is None else (key, alias)
