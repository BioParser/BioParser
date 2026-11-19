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
import re
from collections import defaultdict
from dataclasses import dataclass

import pdfplumber
from pdfminer.encodingdb import name2unicode
from pdfminer.latin_enc import ENCODING
from pdfminer.pdfdocument import PDFDocument
from pdfminer.pdfpage import PDFPage
from pdfminer.pdfparser import PDFParser, PDFSyntaxError
from pdfminer.pdftypes import resolve1

LOGGER = logging.getLogger(__name__)

# A small Adv font has at most this many glyphs. Body text is larger.
# The number is ours. 3B2 does not mark these fonts.
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
    tounicode_id: int | None
    raw: bytes | None
    slots: list[_Slot]
    samples: dict[int, _Sample]
    counts: dict[int, int]


def repair_pdf(data: bytes) -> tuple[bytes, int]:
    """Return the PDF with repaired symbol labels, and how many glyphs became U+FFFD."""
    try:
        fonts = _fonts(data)
    except PDFSyntaxError:
        return data, 0
    interesting = [font for font in fonts if _interesting(font)]
    if not interesting:
        return data, 0
    _attach_samples(data, interesting)
    replacements: list[tuple[_Font, dict[int, str]]] = []
    unmapped: dict[tuple[str, str, str, int], int] = defaultdict(int)
    for font in interesting:
        changed: dict[int, str] = {}
        small = len(font.slots) <= _SMALL_FONT
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
            "unmapped symbol font glyph %s %s on page %s claimed %s (%s times)",
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


def _interesting(font: _Font) -> bool:
    if not _is_advent(font.base):
        return False
    if len(font.slots) <= _SMALL_FONT:
        return True
    return any((font.base, slot.name) in _ROWS for slot in font.slots)


def _decide(base: str, slot: _Slot, *, small: bool) -> str:
    if not _is_advent(base):
        return slot.claimed
    if (base, slot.name) in _ROWS:
        row = _ROWS[(base, slot.name)]
        return _DROP if row is None else row
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
            slots = _slots(font)
            if not slots:
                continue
            tounicode = font.get("ToUnicode")
            tounicode_id = getattr(tounicode, "objid", None)
            found[objid] = _Font(
                base=base,
                objid=objid,
                tounicode_id=tounicode_id if isinstance(tounicode_id, int) else None,
                raw=_object_bytes(data, document, objid),
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


def _slots(font: dict[str, object]) -> list[_Slot]:
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
        slots.append(_Slot(code=code, name=name, claimed=_claimed(name, code)))
    return slots


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


def _claimed(name: str, code: int) -> str:
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
            for char in page.chars:
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
    root, prev, size = _catalog(data)
    if not root:
        return data
    next_id = size
    objects: dict[int, bytes] = {}
    for font, changed in replacements:
        mapping = {slot.code: slot.claimed for slot in font.slots}
        mapping.update(changed)
        cmap = _cmap(mapping)
        if font.tounicode_id is not None:
            objects[font.tounicode_id] = _stream_object(font.tounicode_id, cmap)
            continue
        if font.raw is None:
            continue
        objects[next_id] = _stream_object(next_id, cmap)
        objects[font.objid] = _with_tounicode(font.raw, next_id)
        next_id += 1
    if not objects:
        return data
    return _incremental(data, objects, root, prev, max(size, next_id))


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


def _stream_object(objid: int, payload: bytes) -> bytes:
    header = f"{objid} 0 obj\n<< /Length {len(payload)} >>\nstream\n".encode()
    return header + payload + b"endstream\nendobj\n"


def _with_tounicode(raw: bytes, tounicode_id: int) -> bytes:
    pointer = f"/ToUnicode {tounicode_id} 0 R".encode()
    if b"/ToUnicode" in raw:
        body = re.sub(rb"/ToUnicode\s+\d+\s+\d+\s+R", pointer, raw, count=1)
    else:
        body = raw.replace(b">>", pointer + b">>", 1)
    if not body.endswith(b"\n"):
        body += b"\n"
    return body


def _object_bytes(data: bytes, document: PDFDocument, objid: int) -> bytes | None:
    for xref in document.xrefs:
        try:
            stream, offset, _generation = xref.get_pos(objid)
        except KeyError:
            continue
        if stream is not None or not isinstance(offset, int):
            continue
        end = data.find(b"endobj", offset)
        if end < 0:
            return None
        return data[offset : end + len(b"endobj")] + b"\n"
    return None


def _catalog(data: bytes) -> tuple[bytes, int, int]:
    """The catalog reference, the latest xref offset, and the highest object id."""
    starts = [match.start() for match in re.finditer(rb"startxref", data)]
    if not starts:
        return b"", 0, 1
    prev = int(data[starts[-1] :].split()[1])
    root = b""
    size = 1
    for at in starts:
        window = data[max(0, at - 500) : at]
        found = re.search(rb"/Root\s+\d+\s+\d+\s+R", window)
        if found is not None:
            root = found.group(0)
        size_match = re.search(rb"/Size\s+(\d+)", window)
        if size_match is not None:
            size = max(size, int(size_match.group(1)))
    return root, prev, size


def _incremental(
    data: bytes,
    objects: dict[int, bytes],
    root: bytes,
    prev: int,
    size: int,
) -> bytes:
    out = bytearray(data)
    if not out.endswith(b"\n"):
        out += b"\n"
    positions: dict[int, int] = {}
    for objid in sorted(objects):
        positions[objid] = len(out)
        out += objects[objid]
    xref_at = len(out)
    out += _xref(positions)
    out += (
        f"trailer\n<< /Size {size} {root.decode('ascii')} /Prev {prev} >>\n"
        f"startxref\n{xref_at}\n%%EOF\n"
    ).encode()
    return bytes(out)


def _xref(positions: dict[int, int]) -> bytes:
    ids = sorted(positions)
    runs: list[list[int]] = []
    for objid in ids:
        if runs and objid == runs[-1][-1] + 1:
            runs[-1].append(objid)
        else:
            runs.append([objid])
    parts = [b"xref\n"]
    for run in runs:
        parts.append(f"{run[0]} {len(run)}\n".encode())
        for objid in run:
            parts.append(f"{positions[objid]:010d} 00000 n \n".encode())
    return b"".join(parts)
