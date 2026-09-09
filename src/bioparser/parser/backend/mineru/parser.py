from __future__ import annotations

import json
import tempfile
from pathlib import Path

from bioparser.parser.backend.mineru.mapper import artifact_from_middle_json
from bioparser.parser.backend.mineru.runtime import run_cli_pipeline
from bioparser.parser.checksum import sha256_file
from bioparser.parser.errors import ParserBackendUnavailableError, UnsupportedDocumentError
from bioparser.parser.models import ParserArtifact

MINERU_PARSER_NAME = "mineru"
MINERU_PARSER_VERSION = "1"
PIPELINE_BACKEND = "pipeline"
PARSE_METHOD = "auto"
LANG = "en"


def _find_middle_json(output_dir: Path, stem: str) -> Path:
    matches = sorted(output_dir.rglob(f"{stem}_middle.json"))
    if not matches:
        raise UnsupportedDocumentError("MinerU pipeline finished without writing middle.json.")
    return matches[0]


def artifact_from_cli_output(
    pdf_path: Path,
    output_dir: Path,
    *,
    parser_version: str,
) -> ParserArtifact:
    checksum = sha256_file(pdf_path)
    payload = json.loads(_find_middle_json(output_dir, pdf_path.stem).read_text(encoding="utf-8"))
    try:
        return artifact_from_middle_json(
            payload,
            checksum=checksum,
            parser_name=MINERU_PARSER_NAME,
            parser_version=parser_version,
            configuration={
                "backend": PIPELINE_BACKEND,
                "parse_method": PARSE_METHOD,
                "lang": LANG,
            },
        )
    except ValueError as exc:
        raise UnsupportedDocumentError(str(exc)) from exc


class MinerUParser:
    """Runs the MinerU CLI, then maps middle.json into a parser artifact."""

    def parse(self, path: Path) -> ParserArtifact:
        pdf_path = path.expanduser().resolve()
        if not pdf_path.is_file():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        try:
            with tempfile.TemporaryDirectory(prefix="bioparser-mineru-") as tmp:
                output_dir = Path(tmp)
                run_cli_pipeline(pdf_path, output_dir)
                return artifact_from_cli_output(
                    pdf_path,
                    output_dir,
                    parser_version=MINERU_PARSER_VERSION,
                )
        except ParserBackendUnavailableError:
            raise
        except UnsupportedDocumentError:
            raise
        except Exception as exc:
            raise UnsupportedDocumentError(
                "MinerU failed to parse the PDF; it may be malformed or otherwise unreadable."
            ) from exc
