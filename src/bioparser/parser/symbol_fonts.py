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
from dataclasses import dataclass

import pdfplumber
import pikepdf
from pdfminer.cmapdb import CMapParser, FileUnicodeMap
from pdfminer.encodingdb import name2unicode
from pdfminer.latin_enc import ENCODING
from pdfminer.pdfdocument import PDFDocument
from pdfminer.pdfpage import PDFPage
from pdfminer.pdfparser import PDFParser
from pdfminer.pdftypes import resolve1

LOGGER = logging.getLogger(__name__)

# A small Adv symbol font has at most this many glyphs with ink.
# The number is ours. 3B2 does not mark these fonts. The span of character
# codes is not the count: a WinAnsi font lists every code between the first
# and the last, including ones with no drawing.
_SMALL_FONT = 8
_TEXT_ENCODINGS = frozenset({"WinAnsiEncoding", "MacRomanEncoding", "StandardEncoding"})
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
    # A standard text encoding and no unicode map of its own. Not a symbol font.
    plain: bool
    slots: list[_Slot]
    samples: dict[int, _Sample]
    counts: dict[int, int]


def repair_pdf(data: bytes) -> tuple[bytes, int]:
    """Return the PDF with repaired symbol labels, and how many glyphs became U+FFFD.

    Any failure leaves the original bytes. A PDF that the parsers can already
    read must not become unreadable because the repair could not finish.
    """
    try:
        return _repair(data)
    except Exception:
        LOGGER.warning("symbol font repair left the PDF unchanged", exc_info=True)
        return data, 0


def _repair(data: bytes) -> tuple[bytes, int]:
    fonts = _fonts(data)
    interesting = [font for font in fonts if _interesting(font)]
    if not interesting:
        return data, 0
    _attach_samples(data, interesting)
    replacements: list[tuple[_Font, dict[int, str]]] = []
    unmapped: dict[tuple[str, str, str, int], int] = defaultdict(int)
    for font in interesting:
        changed: dict[int, str] = {}
        small = _is_small(font)
        for slot in font.slots:
            sample = font.samples.get(slot.code)
            if sample is None:
                continue
            decided = _decide(font.base, slot, small=small)
            if decided == slot.claimed:
                continue
            changed[slot.code] = decided
            if decided == _REPLACEMENT:
                unmapped[(font.base, slot.name, slot.claimed, sample.page_index + 1)] += (
                    font.counts.get(slot.code, 1)
                )
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
    if not replacements:
        return data, sum(unmapped.values())
    return _rewrite(data, replacements), sum(unmapped.values())


def _is_advent(base: str) -> bool:
    return base.startswith("Adv")


def _is_small(font: _Font) -> bool:
    # A standard text subset stays as the PDF labels it. Listing every such
    # subset is not possible, and these glyphs are the characters they name.
    if font.plain:
        return False
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
        resources = resolve1(page.resources)
        if not isinstance(resources, dict) or "Font" not in resources:
            continue
        fonts = resolve1(resources["Font"])
        if not isinstance(fonts, dict):
            continue
        for ref in fonts.values():
            objid = getattr(ref, "objid", None)
            if not isinstance(objid, int) or objid in found:
                continue
            font = resolve1(ref)
            if not isinstance(font, dict):
                continue
            base = _base_name(resolve1(font.get("BaseFont")))
            cmap = _cmap_of(font)
            slots = _slots(font, cmap)
            if not slots:
                continue
            found[objid] = _Font(
                base=base,
                objid=objid,
                cmap=cmap,
                ink=_ink_count(font),
                plain=_plain_text(font),
                slots=slots,
                samples={},
                counts={},
            )
    return list(found.values())


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


def _plain_text(font: dict[str, object]) -> bool:
    # No unicode map, and the encoding is a standard one with no renames.
    # The character the PDF gives is the glyph name. Symbol fonts bring a map
    # or a Differences array, and those are the ones the rows cover.
    if font.get("ToUnicode") is not None:
        return False
    encoding = resolve1(font.get("Encoding"))
    if encoding is None or _literal_name(encoding) in _TEXT_ENCODINGS:
        return True
    if not isinstance(encoding, dict):
        return False
    differences = resolve1(encoding.get("Differences"))
    if isinstance(differences, list) and differences:
        return False
    base = _literal_name(resolve1(encoding.get("BaseEncoding")))
    return base is None or base in _TEXT_ENCODINGS


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
    if encoding is None or _literal_name(encoding) == "WinAnsiEncoding":
        return dict(_WIN_NAMES)
    if not isinstance(encoding, dict):
        return {}
    base = _literal_name(resolve1(encoding.get("BaseEncoding")))
    names = dict(_WIN_NAMES) if base == "WinAnsiEncoding" else {}
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


def _attach_samples(data: bytes, fonts: list[_Font]) -> None:
    by_base: dict[str, list[_Font]] = defaultdict(list)
    for font in fonts:
        by_base[font.base].append(font)
    claimed = {(font.base, slot.claimed): slot.code for font in fonts for slot in font.slots}
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for index, page in enumerate(pdf.pages):
            try:
                chars = page.chars
            finally:
                page.close()
            for char in chars:
                base = _base_name(char.get("fontname"))
                text = str(char.get("text", ""))
                code = claimed.get((base, text))
                if code is None:
                    continue
                for font in by_base.get(base, []):
                    if all(slot.code != code for slot in font.slots):
                        continue
                    font.counts[code] = font.counts.get(code, 0) + 1
                    if code in font.samples:
                        continue
                    font.samples[code] = _Sample(page_index=index)


def _rewrite(data: bytes, replacements: list[tuple[_Font, dict[int, str]]]) -> bytes:
    # pikepdf keeps encryption, object streams, and nested dictionaries.
    # The hand-written trailer update dropped those and blanked the pages.
    pdf = pikepdf.open(io.BytesIO(data))
    wrote = False
    for font, changed in replacements:
        mapping = dict(font.cmap)
        if not mapping:
            mapping = {slot.code: slot.claimed for slot in font.slots}
        mapping.update(changed)
        pdf.get_object(font.objid, 0)["/ToUnicode"] = pikepdf.Stream(pdf, _cmap(mapping))
        wrote = True
    if not wrote:
        return data
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
