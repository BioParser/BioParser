"""Find and run the MinerU CLI (isolated from this project's venv)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from bioparser.parser.backend.mineru.schema import (
    MINERU_PIPELINE_VERSION,
    PARSE_METHOD,
    PIPELINE_BACKEND,
)
from bioparser.parser.errors import (
    ParserBackendUnavailableError,
    UnsupportedDocumentError,
)

# Isolated uv tool
# Pin is matching the pipeline middle.json that this backend maps
MINERU_TOOL_SPEC = f"mineru[pipeline]=={MINERU_PIPELINE_VERSION}"


def cli_configuration() -> dict[str, str | int | float | bool]:
    """Knobs actually passed to the MinerU CLI (recorded on the artifact)."""
    return {"backend": PIPELINE_BACKEND, "parse_method": PARSE_METHOD}


def _unavailable_message() -> str:
    return (
        "MinerU CLI not found. Install it with "
        f"`uv tool install --python 3.13 '{MINERU_TOOL_SPEC}' --with six` "
        "and ensure uv's tool directory is on PATH."
    )


def run_cli_pipeline(pdf_path: Path, output_dir: Path) -> None:
    executable = shutil.which("mineru")
    if executable is None:
        raise ParserBackendUnavailableError(_unavailable_message())
    command = [
        executable,
        "-p",
        str(pdf_path),
        "-o",
        str(output_dir),
        "-b",
        PIPELINE_BACKEND,
        "-m",
        PARSE_METHOD,
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise ParserBackendUnavailableError(_unavailable_message()) from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        raise UnsupportedDocumentError(detail or "MinerU CLI failed to parse the PDF.") from exc
