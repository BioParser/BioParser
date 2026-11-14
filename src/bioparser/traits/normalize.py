"""Keys for printed units and printed levels.

A paper prints ``Kg``, ``mm.``, ``km²``, or ``individuals ha-1``. The list names each unit
once, and the key below makes those spellings meet. The key is only for matching. The row
keeps the printed text in its verbatim column and the list's own spelling in ``traitUnit``.

Cleaning is a rule in code: spaces, a trailing period, a superscript or ``^`` exponent, and a
negative exponent. A spelling that cleaning cannot reach, such as ``lbs`` for ``lb``, is an
alias. Aliases are data. They live in the trait list, so the list says everything it accepts.

Case matters for a unit: ``Mg`` is a tonne and ``mg`` is a milligram. A unit is matched with
its case first. Only when nothing matches is case ignored, so ``Kg`` still means ``kg``. The
caller does both passes. ``normalize_unit`` makes the key for one of them. A level ignores case.
"""

import re
import unicodedata
from collections.abc import Mapping

# "individuals ha-1" and "individuals km-2" mean "per hectare" and "per square kilometre".
_NEGATIVE_EXPONENT = re.compile(r"^(?P<top>.+?)[\s·]+(?P<unit>[A-Za-z]+)-(?P<power>[12])$")
_SEPARATORS = re.compile(r"[\s\-_/]+")


def clean_unit(text: str) -> str:
    """The unit's spelling with spaces, exponents and a trailing period normalized. Case stays."""
    cleaned = unicodedata.normalize("NFKC", text).strip().replace("^", "")
    match = _NEGATIVE_EXPONENT.match(cleaned)
    if match is not None:
        power = "" if match["power"] == "1" else match["power"]
        cleaned = f"{match['top']}/{match['unit']}{power}"
    return re.sub(r"\s+", "", cleaned).rstrip(".")


def unit_key(text: str, *, casefold: bool) -> str:
    """The cleaned spelling, with case ignored only when ``casefold`` is set."""
    cleaned = clean_unit(text)
    return cleaned.casefold() if casefold else cleaned


def alias_keys(aliases: Mapping[str, str], *, casefold: bool) -> dict[str, str]:
    """Alias spellings and their targets, cleaned the same way a lookup cleans a unit."""
    return {
        unit_key(spelling, casefold=casefold): unit_key(unit, casefold=casefold)
        for spelling, unit in aliases.items()
    }


def apply_unit(text: str, table: Mapping[str, str], *, casefold: bool) -> str:
    """The key of ``text`` after one alias table. The whole unit is tried, then each side of ``/``."""
    wanted = unit_key(text, casefold=casefold)
    if not table:
        return wanted
    if wanted in table:
        return table[wanted]
    return "/".join(table.get(part, part) for part in wanted.split("/"))


def normalize_unit(text: str, aliases: Mapping[str, str] | None = None, *, casefold: bool) -> str:
    """A key that is the same for every spelling of one unit, with or without its case.

    ``aliases`` maps a spelling to the unit it stands for. The whole unit is tried first, then
    each side of a ``/``. A name that is not an alias stands for itself.
    """
    table = alias_keys(aliases, casefold=casefold) if aliases else {}
    return apply_unit(text, table, casefold=casefold)


def same_unit(left: str, right: str, aliases: Mapping[str, str] | None = None) -> bool:
    """True when two spellings are one unit, first with case and then without."""
    return any(
        normalize_unit(left, aliases, casefold=casefold)
        == normalize_unit(right, aliases, casefold=casefold)
        for casefold in (False, True)
    )


def normalize_level(text: str, aliases: Mapping[str, str] | None = None) -> str:
    """A key that ignores case and the separators between the words of a level.

    ``aliases`` maps a printed form to the level it stands for. A form that is not an alias
    stands for itself.
    """
    key = _SEPARATORS.sub("", unicodedata.normalize("NFKC", text).casefold())
    if not aliases:
        return key
    table = {
        normalize_level(spelling): normalize_level(level) for spelling, level in aliases.items()
    }
    return table.get(key, key)
