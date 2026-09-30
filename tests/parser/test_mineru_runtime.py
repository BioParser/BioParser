import os
import subprocess
import time
from pathlib import Path
from typing import IO, Any

import pytest
from dramatiq.middleware.time_limit import TimeLimitExceeded

from bioparser.parser.backend.mineru import runtime
from bioparser.parser.errors import ParserTimeoutError, UnsupportedDocumentError


def _fake_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: str) -> None:
    executable = tmp_path / "mineru"
    executable.write_text(f"#!/bin/sh\n{script}\n")
    executable.chmod(0o755)
    monkeypatch.setattr(
        runtime, "shutil", type("S", (), {"which": staticmethod(lambda _name: str(executable))})()
    )


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_timeout_kills_the_cli_and_its_children(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid_file = tmp_path / "child.pid"
    _fake_cli(tmp_path, monkeypatch, f"sleep 60 &\necho $! > {pid_file}\nwait")

    with pytest.raises(ParserTimeoutError):
        runtime.run_cli_pipeline(tmp_path / "a.pdf", tmp_path, timeout_s=1.0)

    child = int(pid_file.read_text())
    deadline = time.monotonic() + 5
    while _alive(child) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(child)


def test_nonzero_exit_is_an_unsupported_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_cli(tmp_path, monkeypatch, "echo broken >&2\nexit 1")

    with pytest.raises(UnsupportedDocumentError, match="broken"):
        runtime.run_cli_pipeline(tmp_path / "a.pdf", tmp_path, timeout_s=5.0)


def test_interrupt_kills_the_cli_and_its_children(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Any BaseException while waiting (queue time limit, shutdown) must not orphan MinerU."""
    pid_file = tmp_path / "child.pid"
    _fake_cli(tmp_path, monkeypatch, f"sleep 60 &\necho $! > {pid_file}\nwait")

    real_popen = subprocess.Popen

    def popen(
        command: list[str],
        *,
        stdin: IO[Any] | int | None = None,
        stdout: IO[Any] | int | None = None,
        stderr: IO[Any] | int | None = None,
        text: bool | None = None,
        start_new_session: bool = False,
    ) -> subprocess.Popen[str]:
        process: subprocess.Popen[str] = real_popen(
            command,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            text=text,
            start_new_session=start_new_session,
        )

        def communicate(input: str | None = None, timeout: float | None = None) -> tuple[str, str]:
            # Give the shell time to start its child before "the queue" interrupts.
            deadline = time.monotonic() + 5
            while not pid_file.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            raise TimeLimitExceeded

        process.communicate = communicate  # type: ignore[method-assign]
        return process

    monkeypatch.setattr(subprocess, "Popen", popen)

    with pytest.raises(TimeLimitExceeded):
        runtime.run_cli_pipeline(tmp_path / "a.pdf", tmp_path, timeout_s=30.0)

    child = int(pid_file.read_text())
    deadline = time.monotonic() + 5
    while _alive(child) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(child)
