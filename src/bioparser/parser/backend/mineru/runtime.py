"""Find and run the MinerU CLI and its API server (isolated from this project's venv)."""

from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import IO

from bioparser.parser.backend.mineru.schema import (
    MINERU_PIPELINE_VERSION,
    PARSE_METHOD,
    PIPELINE_BACKEND,
)
from bioparser.parser.errors import (
    ParserBackendUnavailableError,
    ParserProcessError,
    ParserTimeoutError,
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
# How long mineru-api may take to load its models and answer /health.
_STARTUP_TIMEOUT_S = 300.0


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


def mineru_api() -> str | None:
    """Path of the MinerU API server executable, or None when it is not on PATH."""
    return shutil.which("mineru-api")


def require_mineru_cli() -> None:
    """Fail when the MinerU CLI or its server is not installed."""
    if mineru_cli() is None or mineru_api() is None:
        raise RuntimeError(_unavailable_message())


def run_cli_pipeline(pdf_path: Path, output_dir: Path, timeout_s: float | None = None) -> None:
    """Parse a PDF with the CLI against a server this call starts and always stops.

    The CLI would otherwise start its own `mineru-api` in a new session, out of our reach.
    Starting the server here means both processes are ours to kill. After `timeout_s`
    seconds (None means no limit), counted from the server start, they are killed.
    """
    executable, api_executable = mineru_cli(), mineru_api()
    if executable is None or api_executable is None:
        raise ParserBackendUnavailableError(_unavailable_message())
    deadline = None if timeout_s is None else time.monotonic() + timeout_s
    env = _cli_environment()
    # The server is on loopback. A proxy in the environment must not be asked to reach it.
    env["NO_PROXY"] = ",".join(filter(None, [env.get("NO_PROXY"), "127.0.0.1,localhost"]))
    env["no_proxy"] = env["NO_PROXY"]
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    server_command = [api_executable, "--host", "127.0.0.1", "--port", str(port)]
    cli_command = [
        executable,
        "-p",
        str(pdf_path),
        "-o",
        str(output_dir),
        "--api-url",
        base_url,
        "-b",
        PIPELINE_BACKEND,
        "-m",
        PARSE_METHOD,
    ]
    # Output goes to files, not pipes: nothing has to read it while the processes run.
    # mineru-api keeps every upload and result under its output root until its own cleanup
    # runs, 24 hours on. It is killed long before that, so the root is ours to delete.
    with (
        tempfile.TemporaryDirectory(prefix="bioparser-mineru-server-") as server_root,
        tempfile.TemporaryFile() as server_log,
        tempfile.TemporaryFile() as cli_log,
    ):
        server_env = {**env, "MINERU_API_OUTPUT_ROOT": server_root}
        server: subprocess.Popen[bytes] | None = None
        cli: subprocess.Popen[bytes] | None = None
        try:
            server = _start(server_command, server_env, server_log)
            _wait_healthy(server, base_url, deadline, timeout_s, server_log)
            cli = _start(cli_command, env, cli_log)
            _wait_for_cli(cli, server, deadline, timeout_s, server_log)
        finally:
            # Includes the queue's TimeLimitExceeded and shutdown interrupts.
            started = [process for process in (cli, server) if process is not None]
            # Signal every group before waiting on any, so a slow reap cannot spare the server.
            for process in started:
                _signal_group(process)
            for process in started:
                process.wait()
        assert cli is not None
        if cli.returncode == 0:
            return
        detail = _tail(cli_log)
        if cli.returncode < 0:
            raise ParserProcessError(
                f"MinerU CLI was killed by signal {-cli.returncode}. {detail}".rstrip()
            )
        # Exit status 1 is MinerU's code for every error: an unreadable PDF and a transient
        # failure look the same, and only the message tells them apart. We do not match on
        # messages, so a non-zero exit is never judged as a bad document. It is retried,
        # and a PDF that really is bad ends as a failed job (internal_error) once the job's
        # claims run out.
        raise ParserProcessError(
            f"MinerU CLI exited with status {cli.returncode}. {detail}".rstrip()
        )


def _start(command: list[str], env: dict[str, str], log: IO[bytes]) -> subprocess.Popen[bytes]:
    """Run a command as the leader of its own session, so its whole group can be killed."""
    try:
        return subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        raise ParserBackendUnavailableError(_unavailable_message()) from exc


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _expired(deadline: float | None) -> bool:
    return deadline is not None and time.monotonic() >= deadline


def _timeout(timeout_s: float | None) -> ParserTimeoutError:
    return ParserTimeoutError(f"MinerU ran longer than {timeout_s} s and was killed.")


def _server_died(server: subprocess.Popen[bytes], log: IO[bytes]) -> ParserProcessError:
    return ParserProcessError(
        f"mineru-api exited with status {server.returncode}. {_tail(log)}".rstrip()
    )


def _wait_healthy(
    server: subprocess.Popen[bytes],
    base_url: str,
    deadline: float | None,
    timeout_s: float | None,
    log: IO[bytes],
) -> None:
    """Wait until the server answers /health. Loading the models can take a while."""
    startup_deadline = time.monotonic() + _STARTUP_TIMEOUT_S
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while True:
        if server.poll() is not None:
            raise _server_died(server, log)
        try:
            with opener.open(f"{base_url}/health", timeout=1) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            pass
        if _expired(deadline):
            raise _timeout(timeout_s)
        if time.monotonic() >= startup_deadline:
            raise ParserProcessError(
                f"mineru-api was not healthy after {_STARTUP_TIMEOUT_S} s. {_tail(log)}".rstrip()
            )
        time.sleep(0.1)


def _wait_for_cli(
    cli: subprocess.Popen[bytes],
    server: subprocess.Popen[bytes],
    deadline: float | None,
    timeout_s: float | None,
    server_log: IO[bytes],
) -> None:
    """Wait for the CLI to exit. A server that dies first is an error: the CLI would only hang."""
    while True:
        try:
            cli.wait(timeout=0.1)
            return
        except subprocess.TimeoutExpired:
            pass
        if server.poll() is not None:
            raise _server_died(server, server_log)
        if _expired(deadline):
            raise _timeout(timeout_s)


def _signal_group(process: subprocess.Popen[bytes]) -> None:
    """SIGKILL the process's whole group.

    The group id is the pid, because the process leads its own session. Members can outlive
    the leader, and the pid is not reused while any member exists, so this is safe to do
    after the leader has exited too.
    """
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _tail(log: IO[bytes]) -> str:
    """The end of a process's output, where the error is. MinerU can print a lot."""
    log.flush()
    size = log.seek(0, os.SEEK_END)
    log.seek(max(0, size - 4 * _DETAIL_MAX_CHARS))
    text = log.read().decode("utf-8", errors="replace").strip()
    if len(text) <= _DETAIL_MAX_CHARS:
        return text
    return "..." + text[-_DETAIL_MAX_CHARS:]
