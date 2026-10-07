import os
import subprocess
import time
from pathlib import Path
from typing import IO, Any

import pytest
from dramatiq.middleware.time_limit import TimeLimitExceeded

from bioparser.parser.backend.mineru import parser as mineru_parser
from bioparser.parser.backend.mineru import runtime
from bioparser.parser.backend.mineru.parser import MinerUParser
from bioparser.parser.errors import (
    ParserProcessError,
    ParserTimeoutError,
    UnsupportedDocumentError,
)


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


def test_require_mineru_cli_fails_when_it_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        runtime, "shutil", type("S", (), {"which": staticmethod(lambda _name: None)})()
    )

    with pytest.raises(RuntimeError, match="MinerU CLI not found"):
        runtime.require_mineru_cli()


def test_nonzero_exit_is_an_unsupported_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_cli(tmp_path, monkeypatch, "echo broken >&2\nexit 1")

    with pytest.raises(UnsupportedDocumentError, match="broken"):
        runtime.run_cli_pipeline(tmp_path / "a.pdf", tmp_path, timeout_s=5.0)


def test_signal_kill_is_a_process_error_not_a_document_problem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_cli(tmp_path, monkeypatch, "kill -9 $$")

    with pytest.raises(ParserProcessError, match="signal 9"):
        runtime.run_cli_pipeline(tmp_path / "a.pdf", tmp_path, timeout_s=5.0)


def test_long_cli_output_is_cut_to_its_tail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_cli(
        tmp_path,
        monkeypatch,
        "head -c 100000 /dev/zero | tr '\\0' 'a' >&2\necho the-error >&2\nexit 1",
    )

    with pytest.raises(UnsupportedDocumentError) as failure:
        runtime.run_cli_pipeline(tmp_path / "a.pdf", tmp_path, timeout_s=5.0)

    message = str(failure.value)
    assert message.endswith("the-error")
    assert len(message) < 3000


def test_output_that_is_not_utf8_does_not_break_the_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_cli(tmp_path, monkeypatch, "printf '\\377\\376 bad\\n' >&2\nexit 1")

    with pytest.raises(UnsupportedDocumentError, match="bad"):
        runtime.run_cli_pipeline(tmp_path / "a.pdf", tmp_path, timeout_s=5.0)


def test_cli_gets_only_the_environment_it_needs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = tmp_path / "env.txt"
    _fake_cli(tmp_path, monkeypatch, f"env > {seen}")
    monkeypatch.setenv("BIOPARSER_REDIS_URL", "redis://:secret@redis:6379/0")
    monkeypatch.setenv("MINERU_MODEL_SOURCE", "local")
    monkeypatch.setenv("HF_HOME", "/models")

    runtime.run_cli_pipeline(tmp_path / "a.pdf", tmp_path, timeout_s=5.0)

    names = {line.partition("=")[0] for line in seen.read_text().splitlines()}
    assert {"PATH", "MINERU_MODEL_SOURCE", "HF_HOME"} <= names
    assert not {name for name in names if name.startswith("BIOPARSER_")}


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
        errors: str | None = None,
        env: dict[str, str] | None = None,
        start_new_session: bool = False,
    ) -> subprocess.Popen[str]:
        process: subprocess.Popen[str] = real_popen(
            command,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            text=text,
            errors=errors,
            env=env,
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


@pytest.mark.parametrize("error", [OSError("disk full"), MemoryError()])
def test_resource_errors_are_not_reported_as_an_unreadable_pdf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    def run(*_args: object, **_kwargs: object) -> None:
        raise error

    monkeypatch.setattr(mineru_parser, "run_cli_pipeline", run)

    with pytest.raises(type(error)):
        MinerUParser().parse(pdf)
