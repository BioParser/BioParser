"""Advent symbol fonts are repaired before either parser reads the PDF."""

import asyncio
import hashlib
import io
import logging
from pathlib import Path

import pdfplumber
import pikepdf
import pytest

from bioparser.parser.advent_repair import symbol_child
from bioparser.parser.advent_repair.symbol_fonts import (
    _DROP,
    _REPLACEMENT,
    _TEXT_FONTS,
    _cmap,
    _decide,
    _encoding_names,
    _Font,
    _interesting,
    _is_small,
    _Slot,
    repair_pdf,
)
from bioparser.parser.backend.default.parser import DefaultParser
from bioparser.parser.backend.mineru import parser as mineru_parser
from bioparser.parser.backend.mineru.parser import MinerUParser
from bioparser.parser.errors import ParserProcessError, UnsupportedDocumentError
from bioparser.parser.models import ParserArtifact, RepairStatus
from bioparser.services.mineru.client import MinerUError

FIXTURES = Path(__file__).parent / "fixtures"
BIOLOGY = FIXTURES / "plos-biology-1002000.pdf"
EDITORIAL = FIXTURES / "plos-biology-3000248.pdf"
GENETICS = FIXTURES / "pgen-1000076.pdf"
ONE = FIXTURES / "pone-0090887.pdf"


def _text(data: bytes) -> str:
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def _artifact_text(path: Path) -> tuple[str, ParserArtifact]:
    artifact = DefaultParser().parse_in_this_process(path)
    text = "\n".join(
        block.content.text
        for page in artifact.pages
        for block in page.blocks
        if block.content.type == "text"
    )
    return text, artifact


def test_the_2014_biology_paper_recovers_the_symbol_glyphs() -> None:
    text, artifact = _artifact_text(BIOLOGY)
    assert artifact.schema_version == "2"
    assert artifact.parser.version == "3"
    assert artifact.unrepaired_count == 0
    assert artifact.repair_status is RepairStatus.SUCCESS
    assert artifact.checksum == hashlib.sha256(BIOLOGY.read_bytes()).hexdigest()
    assert "(>10 g)" in text
    assert "GI<1.47" in text
    assert "1.36\u00b10.16" in text
    assert "\u2297" in text
    assert "\u00a4" not in text
    assert "\u221a" in text
    assert "\ufb03" not in text
    assert "\x00" not in text
    assert "(cid:" not in text
    assert "\ufffd" not in text


def test_the_2019_editorial_is_left_unchanged() -> None:
    original = EDITORIAL.read_bytes()
    repaired = repair_pdf(original)
    assert repaired.data == original
    assert repaired.count == 0
    assert repaired.status is RepairStatus.SKIPPED
    text, artifact = _artifact_text(EDITORIAL)
    assert artifact.unrepaired_count == 0
    assert artifact.repair_status is RepairStatus.SKIPPED
    assert "Broadening the scope of PLOS Biology" in text


def test_the_2008_genetics_paper_reuses_the_same_glyphs() -> None:
    repaired = repair_pdf(GENETICS.read_bytes())
    text = _text(repaired.data)
    assert "Atm\u2212/\u2212" in text
    assert "p<0.0001" in text
    assert ">90%" in text
    assert "37\u00b0C" in text
    assert "\u03b2-tubulin" in text
    assert "\u03b3H2AX" in text
    assert "\u00b5m" in text
    assert "\u2297" in text
    assert "\u00a4" not in text
    assert repaired.count == 0
    assert repaired.status is RepairStatus.SUCCESS
    assert "\ufffd" not in text


def test_the_2014_one_paper_recovers_the_symbol_glyphs() -> None:
    repaired = repair_pdf(ONE.read_bytes())
    text = _text(repaired.data)
    assert "P<0.05" in text
    assert "P,0.05" not in text
    assert ">80%" in text
    assert "mean\u00b1SEM" in text
    assert "mean6SEM" not in text
    assert "Kg\u00b7day" in text
    assert "17\u00b0C" in text
    assert "\u00b5l" in text
    assert "(\u03b1)" in text
    assert "PP1\u03b32" in text
    assert "arcsin \u221ax" in text
    assert "Strzez\u0307ek" in text
    assert "\u00d7100" in text
    assert repaired.count == 0
    assert repaired.status is RepairStatus.SUCCESS
    assert "\ufffd" not in text


def test_a_saved_row_is_the_font_and_glyph_name() -> None:
    slot = _Slot(code=63, name="question", claimed="?")
    assert _decide("AdvP7DA6", slot, small=True) == "\u00b7"


def test_a_small_advent_glyph_with_no_row_is_refused() -> None:
    # The drawing is the EU publications logo, which is not a character.
    slot = _Slot(code=60, name="less", claimed="<")
    assert _decide("AdvP101F3A", slot, small=True) == _REPLACEMENT


def test_a_row_in_a_large_font_is_applied() -> None:
    slot = _Slot(code=45, name="hyphen", claimed="-")
    assert _decide("AdvP3D3256", slot, small=False) == "\u201d"


def test_a_row_that_matches_the_claim_is_kept() -> None:
    slot = _Slot(code=45, name="hyphen", claimed="-")
    assert _decide("AdvTimes", slot, small=True) == "-"


def test_an_accent_stored_before_its_letter_is_dropped() -> None:
    slot = _Slot(code=128, name="Euro", claimed="\u20ac")
    assert _decide("AdvP4C4E59", slot, small=True) == _DROP


def test_a_small_font_that_is_not_advent_is_kept() -> None:
    slot = _Slot(code=45, name="endash", claimed="\u2013")
    assert _decide("20", slot, small=True) == "\u2013"


def test_a_wide_code_range_with_few_drawings_is_small() -> None:
    font = _Font(
        base="AdvP3D3256",
        objid=1,
        cmap={},
        ink=2,
        slots=[_Slot(code, "A", "A") for code in range(45, 55)],
        samples={},
        counts={},
    )
    assert _is_small(font)


def test_a_small_text_subset_keeps_the_characters_it_names() -> None:
    font = _Font(
        base="AdvP497E2",
        objid=1,
        cmap={},
        ink=6,
        slots=[_Slot(code, "zero", "0") for code in range(40, 52)],
        samples={},
        counts={},
    )
    assert _is_small(font)
    assert "AdvP497E2" in _TEXT_FONTS
    slot = _Slot(code=48, name="parenleft", claimed="(")
    assert _decide("AdvP497E2", slot, small=True) == "("
    letter = _Slot(code=80, name="P", claimed="P")
    assert _decide("AdvP4041BD", letter, small=True) == "P"
    assert _decide("AdvTT3978f921.BI", letter, small=True) == "P"


def test_an_unseen_small_face_becomes_the_replacement_character() -> None:
    letter = _Slot(code=80, name="P", claimed="P")
    assert _decide("AdvUnseen", letter, small=True) == _REPLACEMENT
    unnamed = _Slot(code=1, name="C14", claimed="(cid:1)")
    assert _decide("AdvP4041BD", unnamed, small=True) == _REPLACEMENT


class _EncodingName:
    def __init__(self, name: str) -> None:
        self.name = name


def test_macroman_and_standard_encodings_name_their_glyphs() -> None:
    mac = _encoding_names({"Encoding": _EncodingName("MacRomanEncoding")})
    standard = _encoding_names({"Encoding": _EncodingName("StandardEncoding")})
    win = _encoding_names({"Encoding": _EncodingName("WinAnsiEncoding")})
    assert mac[65] == "A"
    assert standard[65] == "A"
    assert mac[128] != win[128]


def test_an_unnamed_glyph_in_a_large_font_is_refused() -> None:
    slot = _Slot(code=1, name="C14", claimed="(cid:1)")
    font = _Font(
        base="AdvP49811",
        objid=1,
        cmap={},
        ink=40,
        slots=[slot],
        samples={},
        counts={},
    )
    assert _interesting(font)
    assert _decide("AdvP49811", slot, small=False) == _REPLACEMENT


def test_a_long_cmap_uses_blocks_of_at_most_100() -> None:
    text = _cmap({code: "A" for code in range(101)}).decode("ascii")
    assert text.count("beginbfchar") == 2
    assert "100 beginbfchar" in text
    assert "\n1 beginbfchar" in text


def test_a_large_advent_font_without_a_row_is_kept() -> None:
    slot = _Slot(code=65, name="A", claimed="A")
    assert _decide("AdvP49811", slot, small=False) == "A"


def test_a_pdf_without_a_catalog_is_left_unchanged() -> None:
    original = b"%PDF-1.4\n%%EOF\n"
    repaired = repair_pdf(original)
    assert repaired.data == original
    assert repaired.count == 0
    assert repaired.status is RepairStatus.FAILED


def _advent_font(pdf: pikepdf.Pdf, base: str) -> pikepdf.Object:
    for page in pdf.pages:
        fonts = page.Resources.get("/Font")
        if fonts is None:
            continue
        for font in fonts.values():
            name = str(font.get("/BaseFont", ""))
            if name.endswith(base):
                return font
    raise AssertionError(base)


def test_an_encrypted_pdf_keeps_its_text() -> None:
    source = pikepdf.open(BIOLOGY)
    for encryption in (
        pikepdf.Encryption(owner="owner-secret", user="", R=4, aes=True),
        pikepdf.Encryption(owner="owner-secret", user="", R=3, aes=False, metadata=False),
    ):
        encrypted = io.BytesIO()
        source.save(encrypted, encryption=encryption)
        repaired = repair_pdf(encrypted.getvalue())
        assert repaired.status is RepairStatus.SUCCESS
        assert "1.36\u00b10.16" in _text(repaired.data)
        opened = pikepdf.open(io.BytesIO(repaired.data))
        assert len(opened.pages) > 0
        if encryption.R >= 4:
            assert opened.is_encrypted


def test_a_correct_tounicode_entry_is_not_rebuilt_from_the_glyph_name() -> None:
    pdf = pikepdf.open(BIOLOGY)
    font = _advent_font(pdf, "AdvP7DA6")
    # The glyph `two` is already the minus sign. The other slots stay as their names,
    # so the repair still has work to do and must not put `two` back to the digit.
    font["/ToUnicode"] = pikepdf.Stream(
        pdf,
        b"""/CIDInit /ProcSet findresource begin
12 dict begin
begincmap
/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def
/CMapName /Adobe-Identity-UCS def
/CMapType 2 def
1 begincodespacerange
<00> <FF>
endcodespacerange
4 beginbfchar
<23> <0023>
<2C> <002C>
<2E> <002E>
<32> <2212>
endbfchar
endcmap
CMapName currentdict /CMap defineresource pop
end
end
""",
    )
    edited = io.BytesIO()
    pdf.save(edited)
    repaired = repair_pdf(edited.getvalue())
    twos = []
    with pdfplumber.open(io.BytesIO(repaired.data)) as opened:
        for page in opened.pages:
            for char in page.chars:
                if "AdvP7DA6" in str(char.get("fontname")):
                    twos.append(char["text"])
            page.close()
    assert "2" not in twos
    assert "\u2212" in twos
    assert "<" in twos


def test_tounicode_is_set_on_the_font_not_inside_its_encoding() -> None:
    repaired = repair_pdf(BIOLOGY.read_bytes())
    pdf = pikepdf.open(io.BytesIO(repaired.data))
    font = _advent_font(pdf, "AdvP7DA6")
    assert "/ToUnicode" in font
    assert "/ToUnicode" not in font["/Encoding"]


def test_a_truncated_pdf_is_left_unchanged(tmp_path: Path) -> None:
    original = BIOLOGY.read_bytes()[: len(BIOLOGY.read_bytes()) // 2]
    repaired = repair_pdf(original)
    assert repaired.data == original
    assert repaired.count == 0
    assert repaired.status is RepairStatus.FAILED
    path = tmp_path / "truncated.pdf"
    path.write_bytes(original)
    with pytest.raises(UnsupportedDocumentError):
        DefaultParser().parse_in_this_process(path)


def _with_base_font(source: bytes, old: str, new: str, *, font_name: bool) -> bytes:
    pdf = pikepdf.open(io.BytesIO(source))
    for page in pdf.pages:
        resources = page.get("/Resources")
        if resources is None:
            continue
        fonts = resources.get("/Font")
        if fonts is None:
            continue
        for font in fonts.values():
            if not str(font.get("/BaseFont", "")).endswith(old):
                continue
            font["/BaseFont"] = pikepdf.Name(f"/{new}")
            if font_name and font.get("/FontDescriptor") is not None:
                font.FontDescriptor["/FontName"] = pikepdf.Name(f"/{new}")
    out = io.BytesIO()
    pdf.save(out)
    return out.getvalue()


def test_an_unlisted_plain_symbol_font_becomes_the_replacement_character() -> None:
    edited = _with_base_font(BIOLOGY.read_bytes(), "AdvP3D3256", "AdvUnseen", font_name=False)
    repaired = repair_pdf(edited)
    text = _text(repaired.data)
    assert repaired.status is RepairStatus.SUCCESS
    assert repaired.count > 0
    assert "1.3660.16" not in text
    assert "\ufffd" in text


def _page_as_form(source: bytes, page_index: int) -> bytes:
    pdf = pikepdf.open(io.BytesIO(source))
    page = pdf.pages[page_index]
    form = pikepdf.Stream(pdf, page["/Contents"].read_bytes())
    form["/Type"] = pikepdf.Name.XObject
    form["/Subtype"] = pikepdf.Name.Form
    form["/BBox"] = page["/MediaBox"]
    form["/Resources"] = page["/Resources"]
    page["/Resources"] = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Fm0=form))
    page["/Contents"] = pikepdf.Stream(pdf, b"q /Fm0 Do Q\n")
    out = io.BytesIO()
    pdf.save(out)
    return out.getvalue()


def test_a_font_used_only_inside_a_form_is_repaired() -> None:
    wrapped = _page_as_form(BIOLOGY.read_bytes(), 1)
    before = _text(wrapped)
    assert "GI,1.47" in before
    repaired = repair_pdf(wrapped)
    text = _text(repaired.data)
    assert repaired.status is RepairStatus.SUCCESS
    assert "GI<1.47" in text
    assert "1.36\u00b10.16" in text


def test_the_unrepaired_count_matches_the_glyphs_pdfplumber_reads() -> None:
    # Only the base name changes, so pdfplumber still prints the descriptor name.
    # The replacement characters are what the count is for.
    edited = _with_base_font(BIOLOGY.read_bytes(), "AdvP7DA6", "AdvUnknown", font_name=False)
    repaired = repair_pdf(edited)
    seen = 0
    with pdfplumber.open(io.BytesIO(repaired.data)) as pdf:
        for page in pdf.pages:
            seen += sum(1 for char in page.chars or [] if char.get("text") == "\ufffd")
            page.close()
    assert repaired.count == seen
    assert seen > 0


def test_an_unlisted_adv_font_becomes_the_replacement_character() -> None:
    original = BIOLOGY.read_bytes()
    for also_font_name in (False, True):
        edited = _with_base_font(original, "AdvP7DA6", "AdvUnknown", font_name=also_font_name)
        repaired = repair_pdf(edited)
        assert repaired.status is RepairStatus.SUCCESS
        assert repaired.count > 0
        assert "\ufffd" in _text(repaired.data)


def test_the_parser_child_keeps_the_unmapped_log(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "renamed.pdf"
    path.write_bytes(
        _with_base_font(BIOLOGY.read_bytes(), "AdvP7DA6", "AdvUnknown", font_name=False)
    )
    with caplog.at_level(logging.WARNING):
        artifact = asyncio.run(DefaultParser().parse(path))
    assert artifact.unrepaired_count > 0
    assert artifact.repair_status is RepairStatus.SUCCESS
    assert "unmapped symbol font glyph" in caplog.text
    assert "AdvUnknown" in caplog.text


def test_cancelling_the_repair_kills_its_process(monkeypatch: pytest.MonkeyPatch) -> None:
    killed: list[object] = []

    class Proc:
        def __init__(self) -> None:
            self.returncode: int | None = None

        def kill(self) -> None:
            killed.append(self)
            self.returncode = -9

        async def communicate(self) -> tuple[bytes, bytes]:
            await asyncio.sleep(30)
            return b"", b""

        async def wait(self) -> int:
            return -9

    async def spawn(*_args: object, **_kwargs: object) -> Proc:
        return Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)

    async def run() -> None:
        task = asyncio.create_task(symbol_child.repair_pdf_in_child(BIOLOGY))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert killed


def test_a_failed_repair_child_returns_the_original(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    class Proc:
        returncode = 1

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", b"boom"

        def kill(self) -> None:
            return None

        async def wait(self) -> int:
            return 1

    async def spawn(*_args: object, **_kwargs: object) -> Proc:
        return Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    original = BIOLOGY.read_bytes()
    with caplog.at_level(logging.WARNING):
        repaired = asyncio.run(symbol_child.repair_pdf_in_child(BIOLOGY))
    assert repaired.data == original
    assert repaired.count == 0
    assert repaired.status is RepairStatus.FAILED
    assert "left the PDF unchanged" in caplog.text


def test_mineru_is_sent_a_truncated_pdf_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    original = BIOLOGY.read_bytes()[: len(BIOLOGY.read_bytes()) // 2]
    uploaded: list[bytes] = []

    class Client:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            return None

        async def parse(self, *, filename: str, content: bytes) -> dict[str, object]:
            uploaded.append(content)
            raise MinerUError(filename)

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(mineru_parser, "MinerUClient", Client)
    path = tmp_path / "truncated.pdf"
    path.write_bytes(original)
    with pytest.raises(ParserProcessError):
        asyncio.run(MinerUParser("http://127.0.0.1:9").parse(path))
    assert uploaded == [original]


def test_mineru_is_sent_the_repaired_pdf(monkeypatch: pytest.MonkeyPatch) -> None:
    uploaded: list[bytes] = []

    class Client:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            return None

        async def parse(self, *, filename: str, content: bytes) -> dict[str, object]:
            uploaded.append(content)
            raise MinerUError(filename)

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(mineru_parser, "MinerUClient", Client)
    with pytest.raises(ParserProcessError):
        asyncio.run(MinerUParser("http://127.0.0.1:9").parse(BIOLOGY))

    assert len(uploaded) == 1
    assert "GI<1.47" in _text(uploaded[0])
    assert "1.36\u00b10.16" in _text(uploaded[0])
