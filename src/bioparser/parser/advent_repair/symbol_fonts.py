"""Repair Advent 3B2 symbol fonts before either parser reads the PDF.

The font's glyph name and its unicode map name the slot as an ordinary character.
The drawing is the operator. Only a base name that starts with Adv and a
capital letter is considered, so Advent Pro is left alone. A row is that name
plus the glyph name, and the pair is applied as written. A known text face
keeps the character the map claims, even when the face is small. A known
symbol face refuses an unlisted glyph at any size. A face on neither list
keeps the claimed character, and the drawn glyphs are counted. A `(cid:N)`
becomes U+FFFD. A missing character is safer than a wrong one when the face
is known to draw symbols.
"""

from __future__ import annotations

import io
import logging
import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

import pikepdf
from pdfminer.cmapdb import CMapParser, FileUnicodeMap
from pdfminer.latin_enc import ENCODING
from pdfminer.pdfdocument import PDFDocument
from pdfminer.pdffont import PDFFont, PDFUnicodeNotDefined, Type1FontHeaderParser
from pdfminer.pdfinterp import PDFResourceManager
from pdfminer.pdfpage import PDFPage
from pdfminer.pdfparser import PDFParser
from pdfminer.pdftypes import resolve1
from pdfminer.psexceptions import PSEOF

from bioparser.parser.advent_repair.table import _ROWS, _SYMBOL_FONTS, _TEXT_FONTS
from bioparser.parser.models import ParseProblem, ProblemCode, RepairStatus

LOGGER = logging.getLogger(__name__)

# A small Adv font has at most this many glyphs with ink.
# The number is ours. 3B2 does not mark these fonts. The span of character
# codes is not the count: a WinAnsi font lists every code between the first
# and the last, including ones with no drawing.
_SMALL_FONT_MAX_GLYPHS = 8
_REPLACEMENT = "\ufffd"
# A dropped drawing (the square-root bar) becomes U+0000,
# which the parser can replace or handle as needed.
_DROP = "\u0000"


@dataclass(frozen=True)
class _Slot:
    code: int
    name: str
    claimed: str


@dataclass
class _Font:
    base: str
    objid: int
    # Characters the font's own unicode map already names. Empty when it has none.
    cmap: dict[int, str]
    # Glyphs with a nonzero width. None when the font does not say.
    ink: int | None
    slots: list[_Slot]
    # First page a code was drawn on. The key is the character code.
    samples: dict[int, int]
    counts: dict[int, int]


@dataclass(frozen=True)
class SymbolRepair:
    """The PDF the parsers should read, and what the repair did."""

    data: bytes
    # Glyphs replaced with U+FFFD.
    count: int
    status: RepairStatus
    # Drawn glyphs in a small face that is on neither list. They were kept.
    unchecked: int = 0
    # Pages whose content stream could not be counted.
    uncounted_pages: int = 0


def symbol_font_problems(repaired: SymbolRepair) -> list[ParseProblem]:
    """The artifact problems for one symbol-font repair. An empty list needs no warning."""
    if repaired.status is RepairStatus.FAILED:
        return [ParseProblem(code=ProblemCode.ADVENT_FONTS_UNCHECKED)]
    problems: list[ParseProblem] = []
    if repaired.status is RepairStatus.SUCCESS and repaired.count > 0:
        problems.append(
            ParseProblem(code=ProblemCode.ADVENT_SYMBOLS_REPLACED, count=repaired.count)
        )
    if repaired.status is RepairStatus.SUCCESS and repaired.unchecked > 0:
        problems.append(
            ParseProblem(code=ProblemCode.ADVENT_CHARACTERS_UNVERIFIED, count=repaired.unchecked)
        )
    if repaired.status is RepairStatus.SUCCESS and repaired.uncounted_pages > 0:
        problems.append(
            ParseProblem(code=ProblemCode.ADVENT_PAGES_UNCOUNTED, count=repaired.uncounted_pages)
        )
    return problems


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
        uncounted = _count_uses(pdf, interesting)
        replaced, unmapped, unchecked = _replacements(interesting)
        count = sum(unmapped.values())
        payload = data if not replaced else _rewrite(pdf, replaced)
        return SymbolRepair(payload, count, RepairStatus.SUCCESS, unchecked, uncounted)
    finally:
        pdf.close()


def _replacements(
    fonts: list[_Font],
) -> tuple[list[tuple[_Font, dict[int, str]]], dict[tuple[str, str, str, int], int], int]:
    # Every slot is decided from the font dictionary. A glyph the content
    # stream did not show is still rewritten, so a missed count cannot leave
    # the wrong character in place.
    replacements: list[tuple[_Font, dict[int, str]]] = []
    unmapped: dict[tuple[str, str, str, int], int] = defaultdict(int)
    unchecked = 0
    for font in fonts:
        changed: dict[int, str] = {}
        small = _is_small(font)
        unseen = font.base not in _TEXT_FONTS and font.base not in _SYMBOL_FONTS
        kept = 0
        for slot in font.slots:
            decided = _decide(font.base, slot)
            drawn = font.counts.get(slot.code, 0)
            if unseen and small and decided == slot.claimed and drawn:
                kept += drawn
            if decided == slot.claimed:
                continue
            changed[slot.code] = decided
            sample = font.samples.get(slot.code)
            if decided == _REPLACEMENT and drawn and sample is not None:
                unmapped[(font.base, slot.name, slot.claimed, sample + 1)] += drawn
        if kept:
            LOGGER.warning("unchecked advent face %s drew %s glyphs", font.base, kept)
            unchecked += kept
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
    return replacements, unmapped, unchecked


# 3B2 names put a capital letter after Adv. Advent Pro does not.
_ADVENT_NAME = re.compile(r"Adv[A-Z]")


def _is_advent(base: str) -> bool:
    return _ADVENT_NAME.match(base) is not None


def _is_small(font: _Font) -> bool:
    # A standard encoding does not make a font body text. One-glyph symbol
    # fonts use that encoding too. A known text face is listed by name.
    count = font.ink if font.ink is not None else len(font.slots)
    return count <= _SMALL_FONT_MAX_GLYPHS


def _interesting(font: _Font) -> bool:
    if not _is_advent(font.base):
        return False
    if _is_small(font) or font.base in _SYMBOL_FONTS:
        return True
    if any(slot.claimed.startswith("(cid:") for slot in font.slots):
        return True
    return any((font.base, slot.name) in _ROWS for slot in font.slots)


def _decide(base: str, slot: _Slot) -> str:
    if not _is_advent(base):
        return slot.claimed
    if (base, slot.name) in _ROWS:
        row = _ROWS[(base, slot.name)]
        return _DROP if row is None else row
    if slot.claimed.startswith("(cid:"):
        return _REPLACEMENT
    if base in _TEXT_FONTS:
        return slot.claimed
    # A listed symbol face. Size does not make it text.
    # A face on neither list keeps the claim. The caller counts those glyphs.
    if base in _SYMBOL_FONTS:
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
    if not isinstance(objid, int):
        _log_inline_font(ref)
        return
    if objid in found:
        return
    font = resolve1(ref)
    if not isinstance(font, dict):
        return
    base = _base_name(resolve1(font.get("BaseFont")))
    cmap = _cmap_of(font)
    slots = _slots(objid, font)
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


def _log_inline_font(ref: object) -> None:
    font = resolve1(ref)
    if not isinstance(font, dict) or font.get("BaseFont") is None:
        return
    base = _base_name(resolve1(font.get("BaseFont")))
    LOGGER.warning("symbol font repair skipped an inline font %s", base)


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


def _slots(objid: int, font: dict[str, object]) -> list[_Slot]:
    first = resolve1(font.get("FirstChar"))
    last = resolve1(font.get("LastChar"))
    if not isinstance(first, int) or not isinstance(last, int):
        return []
    # Claimed characters come from pdfminer, so a font with no encoding is read
    # the same way the parser reads it. A Type 1 font file supplies the names.
    reader = PDFResourceManager().get_font(objid, font)
    names = _glyph_names(font)
    slots: list[_Slot] = []
    for code in range(first, last + 1):
        name = names.get(code)
        if name is None:
            continue
        slots.append(_Slot(code=code, name=name, claimed=_claimed(reader, code)))
    return slots


def _claimed(reader: PDFFont, code: int) -> str:
    try:
        return reader.to_unichr(code)
    except PDFUnicodeNotDefined:
        return f"(cid:{code})"


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


def _glyph_names(font: dict[str, object]) -> dict[int, str]:
    # The same choice pdfminer makes. A TrueType font with no encoding is
    # WinAnsi. Anything else with no encoding is Standard, unless the font
    # file itself names the glyphs.
    subtype = _literal_name(resolve1(font.get("Subtype")))
    default = "WinAnsiEncoding" if subtype == "TrueType" else "StandardEncoding"
    if "Encoding" not in font:
        embedded = _embedded_names(font)
        return embedded if embedded is not None else _standard_names(default)
    encoding = resolve1(font.get("Encoding"))
    literal = _literal_name(encoding)
    if isinstance(encoding, dict):
        base = _literal_name(resolve1(encoding.get("BaseEncoding"))) or default
        names = _standard_names(base)
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
    return _standard_names(literal or default)


def _standard_names(encoding: str) -> dict[int, str]:
    columns = {"StandardEncoding": 1, "MacRomanEncoding": 2, "WinAnsiEncoding": 3}
    column = columns.get(encoding, 1)
    names: dict[int, str] = {}
    for row in ENCODING:
        code = row[column]
        if isinstance(code, int):
            names[code] = row[0]
    return names


def _embedded_names(font: dict[str, object]) -> dict[int, str] | None:
    descriptor = resolve1(font.get("FontDescriptor"))
    if not isinstance(descriptor, dict) or "FontFile" not in descriptor:
        return None
    stream = resolve1(descriptor.get("FontFile"))
    read = getattr(stream, "get_data", None)
    if not callable(read):
        return None
    raw = read()
    if not isinstance(raw, bytes) or not raw:
        return None
    length = resolve1(stream.get("Length1")) if hasattr(stream, "get") else None
    if isinstance(length, int) and length > 0:
        raw = raw[:length]
    parser = Type1FontHeaderParser(io.BytesIO(raw))
    names: dict[int, str] = {}
    while True:
        try:
            code, name = parser.nextobject()
        except PSEOF:
            return names
        if isinstance(code, int) and isinstance(name, str):
            names[code] = name


def _literal_name(value: object) -> str | None:
    name = getattr(value, "name", None)
    if isinstance(name, str):
        return name
    return None


def _count_uses(pdf: pikepdf.Pdf, fonts: list[_Font]) -> int:
    # Count by font object, not by the name pdfplumber later prints. /BaseFont
    # and the descriptor's /FontName can differ, and two fonts can claim the
    # same character. The content stream names the font resource, which points
    # at one object.
    by_id = {font.objid: font for font in fonts}
    missed = 0
    for index, page in enumerate(pdf.pages):
        if not _count_container(page, page.get("/Resources"), index, by_id, set(), None):
            missed += 1
    return missed


def _count_container(
    container: pikepdf.Object | pikepdf.Page,
    resources: pikepdf.Object | None,
    page_index: int,
    by_id: dict[int, _Font],
    stack: set[tuple[int, int]],
    font: int | None,
) -> bool:
    objgen = getattr(container, "objgen", None)
    if objgen in stack:
        return True
    if objgen is not None:
        stack.add(objgen)
    try:
        return _count_operators(container, resources, page_index, by_id, stack, font)
    finally:
        if objgen is not None:
            stack.discard(objgen)


def _count_operators(
    container: pikepdf.Object | pikepdf.Page,
    resources: pikepdf.Object | None,
    page_index: int,
    by_id: dict[int, _Font],
    stack: set[tuple[int, int]],
    font: int | None,
) -> bool:
    # q and Q save and restore the font with the rest of the graphics state.
    # A form starts from the font that was current at Do, and Do restores it.
    font_ids = _resource_fonts(resources, by_id)
    current = font
    saved: list[int | None] = []
    try:
        operations = pikepdf.parse_content_stream(container)
    except Exception:
        LOGGER.warning("symbol font repair could not read a content stream", exc_info=True)
        return False
    counted = True
    for instruction in operations:
        operands = getattr(instruction, "operands", ())
        operator = getattr(instruction, "operator", "")
        op = str(operator)
        if op == "q":
            saved.append(current)
            continue
        if op == "Q":
            if saved:
                current = saved.pop()
            continue
        if op == "Do" and operands and resources is not None:
            if not _count_xobject(operands[0], resources, page_index, by_id, stack, current):
                counted = False
            continue
        if op == "Tf" and operands:
            current = font_ids.get(str(operands[0]).lstrip("/"))
            continue
        if current is None or current not in by_id or not operands:
            continue
        active = by_id[current]
        if op in {"Tj", "'"}:
            _count_codes(operands[0], active, page_index)
        elif op == '"':
            _count_codes(operands[-1], active, page_index)
        elif op == "TJ":
            for piece in _pieces(operands[0]):
                _count_codes(piece, active, page_index)
    return counted


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
    font: int | None,
) -> bool:
    xobjects = resources.get("/XObject")
    if xobjects is None:
        return True
    xobject = xobjects.get(str(name))
    if xobject is None or str(xobject.get("/Subtype")) != "/Form":
        return True
    nested = xobject.get("/Resources")
    chosen = nested if nested is not None else resources
    return _count_container(xobject, chosen, page_index, by_id, stack, font)


def _pieces(value: object) -> Iterable[object]:
    if isinstance(value, bytes | str) or not isinstance(value, Iterable):
        return ()
    return value


def _count_codes(value: object, font: _Font, page_index: int) -> None:
    raw = _codes(value)
    for code in raw:
        font.counts[code] = font.counts.get(code, 0) + 1
        if code not in font.samples:
            font.samples[code] = page_index


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


def _live_object(pdf: pikepdf.Pdf, objid: int) -> pikepdf.Object:
    # The generation is not 0 for an incrementally updated file. pdfminer does
    # not keep it, so the object is found here by its id.
    matches = [
        obj
        for obj in pdf.objects
        if getattr(obj, "objgen", None) is not None and obj.objgen[0] == objid
    ]
    if not matches:
        raise KeyError(objid)
    return max(matches, key=lambda obj: int(obj.objgen[1]))


def _rewrite(pdf: pikepdf.Pdf, replacements: list[tuple[_Font, dict[int, str]]]) -> bytes:
    # pikepdf keeps encryption, object streams, and nested dictionaries.
    # The caller only reaches here when a slot changed.
    for font, changed in replacements:
        mapping = dict(font.cmap)
        if not mapping:
            mapping = {slot.code: slot.claimed for slot in font.slots}
        mapping.update(changed)
        _live_object(pdf, font.objid)["/ToUnicode"] = pikepdf.Stream(pdf, _cmap(mapping))
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
    # A bfchar block holds at most 100 entries.
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
    ]
    for start in range(0, len(items), 100):
        block = items[start : start + 100]
        lines.append(f"{len(block)} beginbfchar")
        for code, text in block:
            encoded = text.encode("utf-16-be").hex().upper()
            lines.append(f"<{code:02X}> <{encoded}>")
        lines.append("endbfchar")
    lines.extend(
        [
            "endcmap",
            "CMapName currentdict /CMap defineresource pop",
            "end",
            "end",
            "",
        ]
    )
    return "\n".join(lines).encode("ascii")
