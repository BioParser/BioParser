"""Advent symbol fonts are repaired before either parser reads the PDF."""

import asyncio
import hashlib
import io
from pathlib import Path

import pdfplumber
import pytest

from bioparser.parser.backend.default.parser import DefaultParser
from bioparser.parser.backend.mineru import parser as mineru_parser
from bioparser.parser.backend.mineru.parser import MinerUParser
from bioparser.parser.errors import ParserProcessError
from bioparser.parser.models import ParserArtifact
from bioparser.parser.symbol_fonts import _DROP, _REPLACEMENT, _decide, _Slot, repair_pdf
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
    assert artifact.unmapped_symbol_count == 0
    assert artifact.checksum == hashlib.sha256(BIOLOGY.read_bytes()).hexdigest()
    assert "(>10 g)" in text
    assert "GI<1.47" in text
    assert "1.36\u00b10.16" in text
    assert "\u221a" in text
    assert "\ufb03" not in text
    assert "\x00" not in text
    assert "(cid:" not in text
    assert "\ufffd" not in text


def test_the_2019_editorial_is_left_unchanged() -> None:
    original = EDITORIAL.read_bytes()
    repaired, count = repair_pdf(original)
    assert repaired == original
    assert count == 0
    text, artifact = _artifact_text(EDITORIAL)
    assert artifact.unmapped_symbol_count == 0
    assert "Broadening the scope of PLOS Biology" in text


def test_the_2008_genetics_paper_reuses_the_same_glyphs() -> None:
    repaired, count = repair_pdf(GENETICS.read_bytes())
    text = _text(repaired)
    assert "Atm\u2212/\u2212" in text
    assert "p<0.0001" in text
    assert ">90%" in text
    assert "37\u00b0C" in text
    assert "\u03b2-tubulin" in text
    assert "\u03b3H2AX" in text
    assert "\u00b5m" in text
    assert count == 0
    assert "\ufffd" not in text


def test_the_2014_one_paper_recovers_the_symbol_glyphs() -> None:
    repaired, count = repair_pdf(ONE.read_bytes())
    text = _text(repaired)
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
    assert count == 0
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


def test_a_large_advent_font_without_a_row_is_kept() -> None:
    slot = _Slot(code=65, name="A", claimed="A")
    assert _decide("AdvP49811", slot, small=False) == "A"


def test_a_pdf_without_a_catalog_is_left_unchanged() -> None:
    original = b"%PDF-1.4\n%%EOF\n"
    assert repair_pdf(original) == (original, 0)


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
