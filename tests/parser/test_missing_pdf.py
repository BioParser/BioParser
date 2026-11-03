"""A missing PDF: the parsers leave the path out of the error, the CLI prints it (#109)."""

import asyncio
from pathlib import Path

import pytest

from bioparser.parser import PARSER_BACKENDS, get_parser
from bioparser.scripts import pdfparse

PLANTED_DIR = "planted-secret-dir"


@pytest.mark.parametrize("backend", PARSER_BACKENDS)
def test_missing_pdf_error_holds_no_path(tmp_path: Path, backend: str) -> None:
    missing = tmp_path / PLANTED_DIR / "missing.pdf"

    with pytest.raises(FileNotFoundError) as exc_info:
        asyncio.run(get_parser(backend).parse(missing))

    message = str(exc_info.value)
    assert message == "PDF not found"
    assert PLANTED_DIR not in message


@pytest.mark.parametrize("backend", PARSER_BACKENDS)
def test_cli_prints_the_path_of_a_missing_pdf(
    tmp_path: Path, backend: str, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / PLANTED_DIR / "missing.pdf"

    assert pdfparse.main(["--backend", backend, str(missing)]) == 1

    captured = capsys.readouterr()
    assert captured.err == f"PDF not found: {missing.resolve()}\n"
    assert captured.out == ""
