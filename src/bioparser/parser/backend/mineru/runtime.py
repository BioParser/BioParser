"""Find and run the MinerU CLI (isolated from this project's venv)."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
from pathlib import Path

from bioparser.parser.backend.mineru.schema import (
    MINERU_PIPELINE_VERSION,
    PARSE_METHOD,
    PIPELINE_BACKEND,
)
from bioparser.parser.errors import (
    ParserBackendUnavailableError,
    ParserTimeoutError,
    UnsupportedDocumentError,
)

# Isolated uv tool
# Pin is matching the pipeline middle.json that this backend maps
MINERU_TOOL_SPEC = f"mineru[pipeline]=={MINERU_PIPELINE_VERSION}"


def _unavailable_message() -> str:
    return (
        "MinerU CLI not found. Install it with "
        f"`uv tool install --python 3.13 '{MINERU_TOOL_SPEC}' --with six` "
        "and ensure uv's tool directory is on PATH."
    )


def run_cli_pipeline(pdf_path: Path, output_dir: Path, timeout_s: float | None = None) -> None:
    """Run the CLI. After `timeout_s` seconds (None means no limit) it is killed."""
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
    # Own session, so the whole process group (MinerU spawns workers) can be killed.
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        raise ParserBackendUnavailableError(_unavailable_message()) from exc
    try:
        stdout, stderr = process.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        _kill_group(process)
        raise ParserTimeoutError(
            f"MinerU CLI ran longer than {timeout_s} s and was killed."
        ) from exc
    except BaseException:
        # Includes the queue's TimeLimitExceeded and shutdown interrupts.
        _kill_group(process)
        raise
    # The CLI exited on its own. Stop any children it left behind.
    _signal_group(process)
    if process.returncode != 0:
        detail = (stderr or stdout or "").strip()
        raise UnsupportedDocumentError(detail or "MinerU CLI failed to parse the PDF.")


def _signal_group(process: subprocess.Popen[str]) -> None:
    """SIGKILL the CLI's process group. A group that is already gone or not ours is fine."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _kill_group(process: subprocess.Popen[str]) -> None:
    """Kill the CLI and its children, then reap it so no zombie or pipe is left."""
    _signal_group(process)
    process.communicate()
