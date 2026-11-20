"""Repair Advent 3B2 symbol fonts before either parser reads the PDF.

The font's glyph name and its unicode map name the slot as an ordinary character.
The drawing is the operator. Only fonts whose base name starts with Adv are
considered. A row is that name plus the glyph name, and the pair is applied as
written. A small Adv font glyph with no row becomes U+FFFD, so it is not passed
on as a different character.
"""

from __future__ import annotations

import io
import logging
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

import pikepdf
from pdfminer.cmapdb import CMapParser, FileUnicodeMap
from pdfminer.encodingdb import name2unicode
from pdfminer.latin_enc import ENCODING
from pdfminer.pdfdocument import PDFDocument
from pdfminer.pdfpage import PDFPage
from pdfminer.pdfparser import PDFParser
from pdfminer.pdftypes import resolve1

from bioparser.parser.models import RepairStatus

LOGGER = logging.getLogger(__name__)

# A small Adv symbol font has at most this many glyphs with ink.
# The number is ours. 3B2 does not mark these fonts. The span of character
# codes is not the count: a WinAnsi font lists every code between the first
# and the last, including ones with no drawing.
_SMALL_FONT = 8
_REPLACEMENT = "\ufffd"
# A dropped drawing (the square-root bar) becomes U+0000,
# which the parser can replace or handle as needed.
_DROP = "\u0000"

# None drops the glyph. Each comment names the paper where that glyph is used.
# Costa 2013 is Environ Sci Pollut Res 20:5887-5912 and is not a fixture, and
# neither is NAR 2007. Client numbers are the training PDFs, also not fixtures.
# Tests re-check the glyphs in the CC BY fixtures. Other rows are not copied
# into tests. A rule test may use one of them as its example.
_ROWS: dict[tuple[str, str], str | None] = {
    # Biology 2014, Genetics 2008.
    ("AdvP7DA6", "numbersign"): "\u2264",
    # Biology 2014, Genetics 2008, ONE 2014.
    ("AdvP7DA6", "comma"): "<",
    # Biology 2014, Genetics 2008, ONE 2014.
    ("AdvP7DA6", "period"): ">",
    # Biology 2014, Genetics 2008, ONE 2014.
    ("AdvP7DA6", "two"): "\u2212",
    # ONE 2014. The drawing is the dot in "Kg·day".
    ("AdvP7DA6", "question"): "\u00b7",
    # Biology 2014, Genetics 2008, ONE 2014.
    ("AdvP3D3256", "six"): "\u00b1",
    # ONE 2014. The drawing is the closing quote after "mean ±".
    ("AdvP3D3256", "hyphen"): "\u201d",
    # Biology 2014, Genetics 2008, ONE 2014.
    ("AdvPi1", "six"): "\u00d7",
    # ONE 2014. The drawing is the degree sign in "17°C".
    ("AdvP4072C", "six"): "\u00b0",
    # Genetics 2008, ONE 2014.
    ("AdvP44E6F4", "u"): "\u00b0",
    # ONE 2014.
    ("AdvP4C9F3E", "plusminus"): "\u00b1",
    # Biology 2014, Genetics 2008, ONE 2014, NAR 2007.
    ("AdvP697C", "plus"): "+",
    # ONE 2014.
    ("AdvP697C", "m"): "\u00b5",
    # NAR 2007. The drawing is beta in "β cells".
    ("AdvP697C", "b"): "\u03b2",
    # NAR 2007. The drawing is the copyright sign before the year.
    ("AdvP697C", "C211"): "\u00a9",
    # Biology 2014, Genetics 2008.
    ("AdvP7DB7", "comma"): "\u223c",
    # ONE 2014. The drawing is the radical in "arcsin √x".
    ("AdvP7DB7", "exclam"): "\u221a",
    # Biology 2014, ONE 2014.
    ("AdvP40798", "asciitilde"): "=",
    # Biology 2014, ONE 2014.
    ("AdvP40798", "braceleft"): "\u2212",
    # Biology 2014, ONE 2014.
    ("AdvP40798", "z"): "+",
    # Biology 2014.
    ("AdvP40798", "quotedbl"): "\u00b6",
    # ONE 2014.
    ("AdvP40798", "bar"): "\u00d7",
    # Biology 2014, ONE 2014.
    ("AdvP7CAC", "R"): "\u2192",
    # Biology 2014, Genetics 2008, ONE 2014.
    ("AdvT377", "C223"): "\u00a9",
    # Biology 2014, Genetics 2008.
    ("AdvP3F206A", "period"): "\u2609",
    # Biology 2014, Genetics 2008. The drawing is the circled cross that marks
    # a current address, beside the author and again before the note.
    ("AdvP41461E", "currency"): "\u2297",
    ("AdvP403A40", "currency"): "\u2297",
    # Biology 2014. A small text subset. Each glyph is the character it names.
    ("AdvP497E2", "parenleft"): "(",
    ("AdvP497E2", "parenright"): ")",
    ("AdvP497E2", "asterisk"): "*",
    ("AdvP497E2", "plus"): "+",
    ("AdvP497E2", "comma"): ",",
    ("AdvP497E2", "hyphen"): "-",
    ("AdvP497E2", "period"): ".",
    ("AdvP497E2", "slash"): "/",
    ("AdvP497E2", "zero"): "0",
    ("AdvP497E2", "one"): "1",
    ("AdvP497E2", "two"): "2",
    ("AdvP497E2", "three"): "3",
    # ONE 2014.
    ("AdvP3EAA99", "a"): "\u03b1",
    # Genetics 2008.
    ("AdvP3EAA99", "b"): "\u03b2",
    # Genetics 2008, ONE 2014.
    ("AdvP3EAA99", "c"): "\u03b3",
    # Biology 2014.
    ("AdvP3EAA99", "d"): "\u03b4",
    # Genetics 2008.
    ("AdvP3EAA99", "m"): "\u00b5",
    # Genetics 2008.
    ("AdvP3EAA99", "n"): "\u03bd",
    # Biology 2014.
    ("AdvP3EAA99", "x"): "\u03c7",
    # Biology 2014.
    ("AdvP43B38B", "m"): "\u00b5",
    # Biology 2014.
    ("AdvP43B38B", "p"): "\u03c0",
    # ONE 2014.
    ("AdvP43A417", "a"): "\u03b1",
    # ONE 2014.
    ("AdvP43A417", "b"): "\u03b2",
    # Genetics 2008.
    ("AdvP43A417", "m"): "\u00b5",
    # Genetics 2008.
    ("AdvP43A417", "n"): "\u03bd",
    # Biology 2014, Costa 2013.
    ("AdvP4C4E74", "p"): "\u221a",
    # Costa 2013. Not a fixture.
    ("AdvP4C4E74", "onequarter"): "=",
    # Costa 2013. Not a fixture.
    ("AdvP4C4E74", "thorn"): "+",
    # Costa 2013. Not a fixture.
    ("AdvP4C4E74", "eth"): "(",
    # Costa 2013. Not a fixture.
    ("AdvP4C4E74", "Thorn"): ")",
    # NAR 2007. Not a fixture. The drawing is the prime in "D′".
    ("AdvP4C4E74", "zero"): "\u2032",
    # Costa 2013. Not a fixture. The drawing is "(" in the moment formula.
    ("AdvP4C4E46", "C0"): "(",
    # Costa 2013. Not a fixture. The drawing is ")" in the moment formula.
    ("AdvP4C4E46", "C1"): ")",
    # Costa 2013. Not a fixture. The drawing is the radical before "Area".
    ("AdvP4C4E46", "q"): "\u221a",
    # Biology 2014, Costa 2013. Dropped. This is the bar over a square root.
    ("AdvP4C4E46", "ffi"): None,
    # Costa 2013. Not a fixture. The drawing is a bullet, not an ampersand.
    ("AdvP0005", "ampersand"): "\u2022",
    # ONE 2014, Costa 2013.
    ("AdvP4C4E51", "equal"): "/",
    # ONE 2014, Costa 2013.
    ("AdvP4C4E59", "percent"): "%",
    # ONE 2014. The drawing is the dot over the z in "Strzeżek".
    ("AdvP4C4E59", "underscore"): "\u0307",
    # ONE 2014. The drawing is the dot in "O₂⁻·".
    ("AdvP7CA8", "N"): "\u00b7",
    # Costa 2013. Not a fixture. The drawing is the copyright sign.
    ("AdvPi3", "numbersign"): "\u00a9",
    # Costa 2013. Not a fixture. The drawing is the corresponding-author envelope.
    ("AdvTT4ff65459", "asterisk"): "\u2709",
    # Client 008. The drawing is the en dash in a page range.
    ("AdvPS44A44B", "e"): "\u2013",
    # Client 001 and 008. The drawing is the copyright sign.
    ("AdvPSSym", "C211"): "\u00a9",
    # Client 062. The drawing is the copyright sign.
    ("AdvPSSym", "ordfeminine"): "\u00a9",
    # Client 001 and 109. The drawing is the author asterisk.
    ("AdvCORRESAST", "arrowdblup"): "*",
    # Client 008. The drawing is the corresponding-author asterisk.
    ("AdvP4C4E59", "asterisk"): "*",
    # Client 008. The drawing is the tilde in "~50 mm".
    ("AdvPS3FDD77", "w"): "\u223c",
    # Client 008. The drawing is alpha.
    ("AdvPS3F4C13", "a"): "\u03b1",
    # Client 008. The drawing is greater-than.
    ("AdvP4C4E51", "greater"): ">",
    # Client 008 and 063. The drawing is less-than.
    ("AdvP4C4E51", "less"): "<",
    # Client 008. The drawing is plus-minus.
    ("AdvP4C4E74", "C6"): "\u00b1",
    # Client 008. The drawing is the dagger after a table note.
    ("AdvP4C4E74", "y"): "\u2020",
    # Client 062. The drawing is the minus before a negative number.
    ("AdvP4C4E74", "C0"): "\u2212",
    # Client 048. The drawing is the multiplication sign.
    ("AdvP4C4E74", "C2"): "\u00d7",
    # Client 048, 113, and 146. The drawing is an asterisk.
    ("AdvP4C4E74", "C3"): "*",
    # Client 135 and 146. The drawing is the degree sign.
    ("AdvP4C4E74", "C14"): "\u00b0",
    # Client 135. The drawing is a bullet.
    ("AdvP4C4E74", "C15"): "\u2022",
    # Client 048. The drawing is the vertical bar in the formula.
    ("AdvP4C4E74", "j"): "|",
    # Client 048. The drawing is the summation sign.
    ("AdvP4C4E46", "P"): "\u2211",
    # Client 062, 063, and 146. The acute follows its letter.
    ("AdvP4C4E59", "C19"): "\u0301",
    # Client 062. The drawing is the hyphen in "Extant-only".
    ("AdvTimes", "hyphen"): "-",
    # Client 062. The digits in "Biology 34".
    ("AdvPSHN-MI", "three"): "3",
    ("AdvPSHN-MI", "four"): "4",
    # Client 062. The drawing is a list bullet.
    ("AdvPSMPi6", "d"): "\u2022",
    # Client 063. The drawing is the registered sign.
    ("AdvP80516", "V"): "\u00ae",
    # Client 113. The drawing is the multiplication sign.
    ("AdvTT9f665322", "multiply"): "\u00d7",
    # Client 146. The drawing is beta.
    ("AdvPSMP13", "b"): "\u03b2",
    # Client 146. The drawing is the minus before a negative number.
    ("AdvP7DED", "two"): "\u2212",
    # Client 146. The drawing is the equals sign in "n = 51".
    ("AdvP7DED", "five"): "=",
    # Client 147. The drawing is the degree sign in "n°".
    ("AdvOT310e2d14", "degree"): "\u00b0",
    # Client 063 and 146. The two dots of ä and ö are stored before the letter.
    ("AdvP4C4E59", "Euro"): None,
    # Client 063 and 146. The circumflex of â is stored before the letter.
    ("AdvP4C4E59", "asciicircum"): None,
    # Client 063 and 146. The tilde of ñ is stored before the letter.
    ("AdvP4C4E59", "asciitilde"): None,
    # Client 063. The grave of à is stored before the letter.
    ("AdvP4C4E59", "C18"): None,
    # Client 146. The caron of š is stored before the letter.
    ("AdvP4C4E59", "C20"): None,
    # Client 001. The cedilla of ş is stored before the letter.
    ("AdvSPSASORT", "C223"): None,
    # Client 048. The boxes are empty.
    ("AdvP4C4E46", "C18"): None,
    ("AdvP4C4E46", "C19"): None,
}

_WIN_NAMES: dict[int, str] = {
    win: name for name, _std, _mac, win, _pdf in ENCODING if isinstance(win, int)
}
_MAC_NAMES: dict[int, str] = {
    mac: name for name, _std, mac, _win, _pdf in ENCODING if isinstance(mac, int)
}
_STD_NAMES: dict[int, str] = {
    std: name for name, std, _mac, _win, _pdf in ENCODING if isinstance(std, int)
}
_ENCODING_NAMES = {
    "WinAnsiEncoding": _WIN_NAMES,
    "MacRomanEncoding": _MAC_NAMES,
    "StandardEncoding": _STD_NAMES,
}


@dataclass(frozen=True)
class _Slot:
    code: int
    name: str
    claimed: str


@dataclass(frozen=True)
class _Sample:
    page_index: int


@dataclass
class _Font:
    base: str
    objid: int
    # Characters the font's own unicode map already names. Empty when it has none.
    cmap: dict[int, str]
    # Glyphs with a nonzero width. None when the font does not say.
    ink: int | None
    slots: list[_Slot]
    samples: dict[int, _Sample]
    counts: dict[int, int]


@dataclass(frozen=True)
class SymbolRepair:
    """The PDF the parsers should read, and what the repair did."""

    data: bytes
    count: int
    status: RepairStatus


def repair_pdf(data: bytes) -> SymbolRepair:
    """Repair symbol labels. A failure returns the original bytes and status failed.

    A PDF that the parsers can already read must not become unreadable because
    the repair could not finish.
    """
    try:
        return _repair(data)
    except Exception:
        LOGGER.warning("symbol font repair left the PDF unchanged", exc_info=True)
        return SymbolRepair(data, 0, RepairStatus.FAILED)


def _repair(data: bytes) -> SymbolRepair:
    fonts = _fonts(data)
    interesting = [font for font in fonts if _interesting(font)]
    if not interesting:
        return SymbolRepair(data, 0, RepairStatus.SKIPPED)
    pdf = pikepdf.open(io.BytesIO(data))
    try:
        _count_uses(pdf, interesting)
        replaced, unmapped = _replacements(interesting)
        count = sum(unmapped.values())
        if not replaced:
            return SymbolRepair(data, count, RepairStatus.SUCCESS)
        return SymbolRepair(_rewrite(pdf, replaced), count, RepairStatus.SUCCESS)
    finally:
        pdf.close()


def _replacements(
    fonts: list[_Font],
) -> tuple[list[tuple[_Font, dict[int, str]]], dict[tuple[str, str, str, int], int]]:
    # Every slot is decided from the font dictionary. A glyph the content
    # stream did not show is still rewritten, so a missed count cannot leave
    # the wrong character in place.
    replacements: list[tuple[_Font, dict[int, str]]] = []
    unmapped: dict[tuple[str, str, str, int], int] = defaultdict(int)
    for font in fonts:
        changed: dict[int, str] = {}
        small = _is_small(font)
        for slot in font.slots:
            decided = _decide(font.base, slot, small=small)
            if decided == slot.claimed:
                continue
            changed[slot.code] = decided
            count = font.counts.get(slot.code, 0)
            sample = font.samples.get(slot.code)
            if decided == _REPLACEMENT and count and sample is not None:
                unmapped[(font.base, slot.name, slot.claimed, sample.page_index + 1)] += count
        if changed:
            replacements.append((font, changed))
    for (base, glyph, claimed, page), count in sorted(unmapped.items()):
        LOGGER.warning(
            "unmapped symbol font glyph %s %s first seen on page %s claimed %s (%s times)",
            base,
            glyph,
            page,
            claimed,
            count,
        )
    return replacements, unmapped


def _is_advent(base: str) -> bool:
    return base.startswith("Adv")


def _is_small(font: _Font) -> bool:
    # A standard encoding does not make a font body text. One-glyph symbol
    # fonts use that encoding too. A known text subset has a row for each glyph.
    count = font.ink if font.ink is not None else len(font.slots)
    return count <= _SMALL_FONT


def _interesting(font: _Font) -> bool:
    if not _is_advent(font.base):
        return False
    if _is_small(font):
        return True
    return any((font.base, slot.name) in _ROWS for slot in font.slots)


def _decide(base: str, slot: _Slot, *, small: bool) -> str:
    if not _is_advent(base):
        return slot.claimed
    if (base, slot.name) in _ROWS:
        row = _ROWS[(base, slot.name)]
        return _DROP if row is None else row
    if slot.claimed.startswith("(cid:"):
        return _REPLACEMENT
    if small:
        return _REPLACEMENT
    return slot.claimed


def _fonts(data: bytes) -> list[_Font]:
    parser = PDFParser(io.BytesIO(data))
    document = PDFDocument(parser)
    found: dict[int, _Font] = {}
    for page in PDFPage.create_pages(document):
        # Form XObjects carry their own font dictionaries. A page that only
        # draws a form would otherwise leave those fonts unread.
        _collect_fonts(page.resources, found, set())
    return list(found.values())


def _collect_fonts(resources: object, found: dict[int, _Font], seen: set[int]) -> None:
    resources = resolve1(resources)
    if not isinstance(resources, dict):
        return
    marker = id(resources)
    if marker in seen:
        return
    seen.add(marker)
    fonts = resolve1(resources.get("Font"))
    if isinstance(fonts, dict):
        for ref in fonts.values():
            _remember_font(ref, found)
    xobjects = resolve1(resources.get("XObject"))
    if not isinstance(xobjects, dict):
        return
    for ref in xobjects.values():
        xobject = resolve1(ref)
        if _literal_name(resolve1(_item(xobject, "Subtype"))) != "Form":
            continue
        _collect_fonts(_item(xobject, "Resources"), found, seen)


def _remember_font(ref: object, found: dict[int, _Font]) -> None:
    objid = getattr(ref, "objid", None)
    if not isinstance(objid, int) or objid in found:
        return
    font = resolve1(ref)
    if not isinstance(font, dict):
        return
    base = _base_name(resolve1(font.get("BaseFont")))
    cmap = _cmap_of(font)
    slots = _slots(font, cmap)
    if not slots:
        return
    found[objid] = _Font(
        base=base,
        objid=objid,
        cmap=cmap,
        ink=_ink_count(font),
        slots=slots,
        samples={},
        counts={},
    )


def _item(obj: object, key: str) -> object:
    getter = getattr(obj, "get", None)
    if callable(getter):
        return getter(key)
    return None


def _base_name(value: object) -> str:
    if isinstance(value, bytes):
        text = value.decode("latin1")
    else:
        text = str(value)
    text = text.strip("/'")
    return text.split("+")[-1]


def _slots(font: dict[str, object], cmap: dict[int, str]) -> list[_Slot]:
    first = resolve1(font.get("FirstChar"))
    last = resolve1(font.get("LastChar"))
    if not isinstance(first, int) or not isinstance(last, int):
        return []
    names = _encoding_names(font)
    slots: list[_Slot] = []
    for code in range(first, last + 1):
        name = names.get(code)
        if name is None:
            continue
        slots.append(_Slot(code=code, name=name, claimed=cmap.get(code, _from_name(name, code))))
    return slots


def _ink_count(font: dict[str, object]) -> int | None:
    widths = resolve1(font.get("Widths"))
    if not isinstance(widths, list):
        return None
    return sum(1 for width in widths if isinstance(width, int | float) and width != 0)


def _cmap_of(font: dict[str, object]) -> dict[int, str]:
    stream = resolve1(font.get("ToUnicode"))
    read = getattr(stream, "get_data", None)
    if not callable(read):
        return {}
    raw = read()
    if not isinstance(raw, bytes) or not raw:
        return {}
    parsed = FileUnicodeMap()
    CMapParser(parsed, io.BytesIO(raw)).run()
    return dict(parsed.cid2unichr)


def _encoding_names(font: dict[str, object]) -> dict[int, str]:
    encoding = resolve1(font.get("Encoding"))
    literal = _literal_name(encoding)
    if encoding is None or literal == "WinAnsiEncoding":
        return dict(_WIN_NAMES)
    if literal in _ENCODING_NAMES:
        return dict(_ENCODING_NAMES[literal])
    if not isinstance(encoding, dict):
        return {}
    base = _literal_name(resolve1(encoding.get("BaseEncoding")))
    names = dict(_ENCODING_NAMES[base]) if base in _ENCODING_NAMES else {}
    differences = resolve1(encoding.get("Differences"))
    if not isinstance(differences, list):
        return names
    code = 0
    for item in differences:
        item = resolve1(item)
        if isinstance(item, int):
            code = item
            continue
        name = _literal_name(item)
        if name is not None:
            names[code] = name
            code += 1
    return names


def _literal_name(value: object) -> str | None:
    name = getattr(value, "name", None)
    if isinstance(name, str):
        return name
    return None


def _from_name(name: str, code: int) -> str:
    try:
        return name2unicode(name)
    except KeyError:
        return f"(cid:{code})"


def _count_uses(pdf: pikepdf.Pdf, fonts: list[_Font]) -> None:
    # Count by font object, not by the name pdfplumber later prints. /BaseFont
    # and the descriptor's /FontName can differ, and two fonts can claim the
    # same character. The content stream names the font resource, which points
    # at one object.
    by_id = {font.objid: font for font in fonts}
    for index, page in enumerate(pdf.pages):
        _count_container(page, page.get("/Resources"), index, by_id, set())


def _count_container(
    container: pikepdf.Object | pikepdf.Page,
    resources: pikepdf.Object | None,
    page_index: int,
    by_id: dict[int, _Font],
    stack: set[tuple[int, int]],
) -> None:
    objgen = getattr(container, "objgen", None)
    if objgen in stack:
        return
    if objgen is not None:
        stack.add(objgen)
    try:
        _count_operators(container, resources, page_index, by_id, stack)
    finally:
        if objgen is not None:
            stack.discard(objgen)


def _count_operators(
    container: pikepdf.Object | pikepdf.Page,
    resources: pikepdf.Object | None,
    page_index: int,
    by_id: dict[int, _Font],
    stack: set[tuple[int, int]],
) -> None:
    font_ids = _resource_fonts(resources, by_id)
    current: int | None = None
    try:
        operations = pikepdf.parse_content_stream(container)
    except Exception:
        LOGGER.warning("symbol font repair could not read a content stream", exc_info=True)
        return
    for instruction in operations:
        operands = getattr(instruction, "operands", ())
        operator = getattr(instruction, "operator", "")
        op = str(operator)
        if op == "Do" and operands and resources is not None:
            _count_xobject(operands[0], resources, page_index, by_id, stack)
            continue
        if op == "Tf" and operands:
            current = font_ids.get(str(operands[0]).lstrip("/"))
            continue
        if current is None or current not in by_id or not operands:
            continue
        font = by_id[current]
        if op in {"Tj", "'"}:
            _count_codes(operands[0], font, page_index)
        elif op == '"':
            _count_codes(operands[-1], font, page_index)
        elif op == "TJ":
            for piece in _pieces(operands[0]):
                _count_codes(piece, font, page_index)


def _resource_fonts(resources: pikepdf.Object | None, by_id: dict[int, _Font]) -> dict[str, int]:
    if resources is None:
        return {}
    fonts = resources.get("/Font")
    if fonts is None:
        return {}
    found: dict[str, int] = {}
    for key, font in fonts.items():
        objgen = getattr(font, "objgen", None)
        if objgen is None or objgen[0] not in by_id:
            continue
        found[str(key).lstrip("/")] = objgen[0]
    return found


def _count_xobject(
    name: object,
    resources: pikepdf.Object,
    page_index: int,
    by_id: dict[int, _Font],
    stack: set[tuple[int, int]],
) -> None:
    xobjects = resources.get("/XObject")
    if xobjects is None:
        return
    xobject = xobjects.get(str(name))
    if xobject is None or str(xobject.get("/Subtype")) != "/Form":
        return
    nested = xobject.get("/Resources")
    _count_container(xobject, nested if nested is not None else resources, page_index, by_id, stack)


def _pieces(value: object) -> Iterable[object]:
    if isinstance(value, bytes | str) or not isinstance(value, Iterable):
        return ()
    return value


def _count_codes(value: object, font: _Font, page_index: int) -> None:
    raw = _codes(value)
    for code in raw:
        font.counts[code] = font.counts.get(code, 0) + 1
        if code not in font.samples:
            font.samples[code] = _Sample(page_index=page_index)


def _codes(value: object) -> bytes:
    if isinstance(value, bool | int | float | Decimal):
        return b""
    decode = getattr(value, "__bytes__", None)
    if not callable(decode):
        return b""
    try:
        raw = decode()
    except (TypeError, ValueError):
        return b""
    return raw if isinstance(raw, bytes) else b""


def _rewrite(pdf: pikepdf.Pdf, replacements: list[tuple[_Font, dict[int, str]]]) -> bytes:
    # pikepdf keeps encryption, object streams, and nested dictionaries.
    wrote = False
    for font, changed in replacements:
        mapping = dict(font.cmap)
        if not mapping:
            mapping = {slot.code: slot.claimed for slot in font.slots}
        mapping.update(changed)
        pdf.get_object(font.objid, 0)["/ToUnicode"] = pikepdf.Stream(pdf, _cmap(mapping))
        wrote = True
    if not wrote:
        raise RuntimeError("symbol font repair had nothing to write")
    out = io.BytesIO()
    try:
        # True keeps the encryption the file already has. Older RC4 cannot
        # be written back, so the parser reads a decrypted copy instead.
        pdf.save(out, encryption=pdf.is_encrypted)
    except ValueError:
        out = io.BytesIO()
        pdf.save(out)
    return out.getvalue()


def _cmap(mapping: dict[int, str]) -> bytes:
    items = [(code, text) for code, text in sorted(mapping.items()) if text]
    lines = [
        "/CIDInit /ProcSet findresource begin",
        "12 dict begin",
        "begincmap",
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
        "/CMapName /Adobe-Identity-UCS def",
        "/CMapType 2 def",
        "1 begincodespacerange",
        "<00> <FF>",
        "endcodespacerange",
        f"{len(items)} beginbfchar",
    ]
    for code, text in items:
        encoded = text.encode("utf-16-be").hex().upper()
        lines.append(f"<{code:02X}> <{encoded}>")
    lines.extend(
        [
            "endbfchar",
            "endcmap",
            "CMapName currentdict /CMap defineresource pop",
            "end",
            "end",
            "",
        ]
    )
    return "\n".join(lines).encode("ascii")
