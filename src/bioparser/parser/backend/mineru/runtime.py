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
    ParserProcessError,
    ParserTimeoutError,
    UnsupportedDocumentError,
)

# Isolated uv tool
# Pin is matching the pipeline middle.json that this backend maps
MINERU_TOOL_SPEC = f"mineru[pipeline]=={MINERU_PIPELINE_VERSION}"


# What the CLI may inherit. The worker's own environment holds the Redis URL and other
# credentials that MinerU has no use for.
_ENV_NAMES = frozenset(
    {
        "PATH", "HOME", "USER", "LANG", "LANGUAGE", "TMPDIR", "TEMP", "TMP", "TZ",
        "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE",
        "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
    }
)  # fmt: skip
_ENV_PREFIXES = (
    "LC_", "MINERU_", "HF_", "MODELSCOPE_", "XDG_", "CUDA_", "NVIDIA_", "TORCH_", "OMP_", "MKL_",
)  # fmt: skip
# The tail of the CLI's output that goes into an error message and from there into logs.
_DETAIL_MAX_CHARS = 2000


def _cli_environment() -> dict[str, str]:
    """The part of this process's environment that the MinerU CLI needs."""
    return {
        name: value
        for name, value in os.environ.items()
        if name in _ENV_NAMES or name.startswith(_ENV_PREFIXES)
    }


def _unavailable_message() -> str:
    return (
        "MinerU CLI not found. Install it with "
        f"`uv tool install --python 3.13 '{MINERU_TOOL_SPEC}' --with six` "
        "and ensure uv's tool directory is on PATH."
    )


def mineru_cli() -> str | None:
    """Path of the MinerU executable, or None when it is not on PATH."""
    return shutil.which("mineru")


def require_mineru_cli() -> None:
    """Fail when the MinerU CLI is not installed."""
    if mineru_cli() is None:
        raise RuntimeError(_unavailable_message())


def run_cli_pipeline(pdf_path: Path, output_dir: Path, timeout_s: float | None = None) -> None:
    """Run the CLI. After `timeout_s` seconds (None means no limit) it is killed."""
    executable = mineru_cli()
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
            # A byte that is not valid UTF-8 must not fail the read of the output.
            errors="replace",
            env=_cli_environment(),
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
    if process.returncode < 0:
        # Killed by a signal (for example the OOM killer): says nothing about the PDF.
        raise ParserProcessError(
            f"MinerU CLI was killed by signal {-process.returncode}. {_tail(stderr or stdout)}"
        )
    if process.returncode != 0:
        detail = _tail(stderr or stdout)
        raise UnsupportedDocumentError(detail or "MinerU CLI failed to parse the PDF.")


def _tail(output: str | None) -> str:
    """The end of the CLI's output, where the error is. MinerU can print a lot."""
    text = (output or "").strip()
    if len(text) <= _DETAIL_MAX_CHARS:
        return text
    return "..." + text[-_DETAIL_MAX_CHARS:]


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
