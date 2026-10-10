import os
import sys
import time
from pathlib import Path

import pytest

from bioparser.parser.backend.mineru import parser as mineru_parser
from bioparser.parser.backend.mineru import runtime
from bioparser.parser.backend.mineru.parser import MinerUParser
from bioparser.parser.errors import (
    ParserBackendUnavailableError,
    ParserProcessError,
    ParserTimeoutError,
)

_SERVER_PREAMBLE = f"""#!{sys.executable}
import os, sys
port = int(sys.argv[sys.argv.index("--port") + 1])
with open(os.environ.get("MINERU_FAKE_SERVER_PID", os.devnull), "w") as handle:
    handle.write(str(os.getpid()))
"""

# A mineru-api that answers /health until it is killed.
_HEALTHY_SERVER = (
    _SERVER_PREAMBLE
    + """
import http.server

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args):
        pass

http.server.HTTPServer(("127.0.0.1", port), Handler).serve_forever()
"""
)


def _fake_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cli: str,
    *,
    server: str = _HEALTHY_SERVER,
) -> Path:
    """Fake `mineru` (a shell script) and `mineru-api`. Returns the file the server writes its pid to."""
    tools = {"mineru": f"#!/bin/sh\n{cli}\n", "mineru-api": server}
    paths = {}
    for name, text in tools.items():
        path = tmp_path / name
        path.write_text(text)
        path.chmod(0o755)
        paths[name] = str(path)
    monkeypatch.setattr(runtime, "shutil", type("S", (), {"which": staticmethod(paths.get)})())
    server_pid = tmp_path / "server.pid"
    monkeypatch.setenv("MINERU_FAKE_SERVER_PID", str(server_pid))
    monkeypatch.setenv("PATH", os.environ["PATH"])
    return server_pid


class _Interrupt(BaseException):
    """Stands in for a queue's time-limit interrupt."""


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _wait_until_gone(pid: int) -> None:
    deadline = time.monotonic() + 5
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(pid)


def _run(tmp_path: Path, timeout_s: float = 10.0) -> None:
    runtime.run_cli_pipeline(tmp_path / "a.pdf", tmp_path, timeout_s=timeout_s)


def test_cli_is_pointed_at_the_server_that_was_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = tmp_path / "args.txt"
    _fake_tools(tmp_path, monkeypatch, f'echo "$@" > {args}')

    _run(tmp_path)

    assert "--api-url http://127.0.0.1:" in args.read_text()


def test_server_output_root_is_removed_afterwards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root_file = tmp_path / "root.txt"
    server = _HEALTHY_SERVER.replace(
        "import http.server\n",
        "import http.server\n"
        "root = os.environ['MINERU_API_OUTPUT_ROOT']\n"
        f"open({str(root_file)!r}, 'w').write(root)\n"
        "open(os.path.join(root, 'upload'), 'w').close()\n",
        1,
    )
    _fake_tools(tmp_path, monkeypatch, "exit 0", server=server)
    monkeypatch.chdir(tmp_path)

    _run(tmp_path)

    root = Path(root_file.read_text())
    assert root.is_absolute()
    assert not root.exists()
    assert not (tmp_path / "output").exists()


def test_server_is_stopped_after_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server_pid = _fake_tools(tmp_path, monkeypatch, "exit 0")

    _run(tmp_path)

    _wait_until_gone(int(server_pid.read_text()))


def test_server_is_stopped_after_cli_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_pid = _fake_tools(tmp_path, monkeypatch, "echo broken >&2\nexit 1")

    with pytest.raises(ParserProcessError, match="broken"):
        _run(tmp_path)

    _wait_until_gone(int(server_pid.read_text()))


def test_timeout_kills_the_cli_its_children_and_the_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    child_pid = tmp_path / "child.pid"
    server_pid = _fake_tools(tmp_path, monkeypatch, f"sleep 60 &\necho $! > {child_pid}\nwait")

    with pytest.raises(ParserTimeoutError):
        _run(tmp_path, timeout_s=1.5)

    _wait_until_gone(int(child_pid.read_text()))
    _wait_until_gone(int(server_pid.read_text()))


def test_interrupt_kills_the_cli_and_the_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Any BaseException while waiting (queue time limit, shutdown) must not orphan MinerU."""
    child_pid = tmp_path / "child.pid"
    server_pid = _fake_tools(tmp_path, monkeypatch, f"sleep 60 &\necho $! > {child_pid}\nwait")

    def interrupted(*_args: object, **_kwargs: object) -> None:
        deadline = time.monotonic() + 5
        while not child_pid.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        raise _Interrupt

    monkeypatch.setattr(runtime, "_wait_for_cli", interrupted)

    with pytest.raises(_Interrupt):
        _run(tmp_path, timeout_s=30.0)

    _wait_until_gone(int(child_pid.read_text()))
    _wait_until_gone(int(server_pid.read_text()))


def test_server_dying_while_the_cli_runs_is_a_process_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dying = _HEALTHY_SERVER.replace(
        "http.server.HTTPServer(",
        "import threading, time\n"
        "threading.Thread(target=lambda: (time.sleep(1), os._exit(3)), daemon=True).start()\n"
        "http.server.HTTPServer(",
    )
    child_pid = tmp_path / "child.pid"
    _fake_tools(tmp_path, monkeypatch, f"sleep 60 &\necho $! > {child_pid}\nwait", server=dying)

    with pytest.raises(ParserProcessError, match="mineru-api exited with status 3"):
        _run(tmp_path)

    # The CLI would have hung on the dead server. It is stopped too.
    _wait_until_gone(int(child_pid.read_text()))


def test_server_that_never_becomes_healthy_is_a_process_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_pid = _fake_tools(
        tmp_path, monkeypatch, "exit 0", server=_SERVER_PREAMBLE + "import time\ntime.sleep(60)\n"
    )
    monkeypatch.setattr(runtime, "_STARTUP_TIMEOUT_S", 0.5)

    with pytest.raises(ParserProcessError, match="not healthy"):
        _run(tmp_path)

    _wait_until_gone(int(server_pid.read_text()))


def test_server_that_exits_at_startup_is_a_process_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_tools(
        tmp_path,
        monkeypatch,
        "exit 0",
        server=_SERVER_PREAMBLE + "print('address in use', file=sys.stderr)\nsys.exit(1)\n",
    )

    with pytest.raises(ParserProcessError, match="address in use"):
        _run(tmp_path)


def test_require_mineru_cli_fails_when_it_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        runtime, "shutil", type("S", (), {"which": staticmethod(lambda _name: None)})()
    )

    with pytest.raises(RuntimeError, match="MinerU CLI not found"):
        runtime.require_mineru_cli()


def test_require_mineru_cli_fails_when_the_server_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        runtime,
        "shutil",
        type(
            "S", (), {"which": staticmethod(lambda name: "/x/mineru" if name == "mineru" else None)}
        )(),
    )

    with pytest.raises(RuntimeError, match="MinerU CLI not found"):
        runtime.require_mineru_cli()
    with pytest.raises(ParserBackendUnavailableError):
        _run(tmp_path)


def test_nonzero_exit_is_a_process_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_tools(tmp_path, monkeypatch, "echo broken >&2\nexit 1")

    with pytest.raises(ParserProcessError, match="broken"):
        _run(tmp_path)


def test_signal_kill_is_a_process_error_not_a_document_problem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_tools(tmp_path, monkeypatch, "kill -9 $$")

    with pytest.raises(ParserProcessError, match="signal 9"):
        _run(tmp_path)


def test_long_cli_output_is_cut_to_its_tail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_tools(
        tmp_path,
        monkeypatch,
        "head -c 100000 /dev/zero | tr '\\0' 'a' >&2\necho the-error >&2\nexit 1",
    )

    with pytest.raises(ParserProcessError) as failure:
        _run(tmp_path)

    message = str(failure.value)
    assert message.endswith("the-error")
    assert len(message) < 3000


def test_output_that_is_not_utf8_does_not_break_the_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_tools(tmp_path, monkeypatch, "printf '\\377\\376 bad\\n' >&2\nexit 1")

    with pytest.raises(ParserProcessError, match="bad"):
        _run(tmp_path)


def test_cli_gets_only_the_environment_it_needs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = tmp_path / "env.txt"
    _fake_tools(tmp_path, monkeypatch, f"env > {seen}")
    monkeypatch.setenv("BIOPARSER_REDIS_URL", "redis://:secret@redis:6379/0")
    monkeypatch.setenv("MINERU_MODEL_SOURCE", "local")
    monkeypatch.setenv("HF_HOME", "/models")
    monkeypatch.setenv("HTTP_PROXY", "http://proxy:3128")

    _run(tmp_path)

    env = dict(line.partition("=")[::2] for line in seen.read_text().splitlines())
    assert {"PATH", "MINERU_MODEL_SOURCE", "HF_HOME"} <= env.keys()
    assert not {name for name in env if name.startswith("BIOPARSER_")}
    assert "127.0.0.1" in env["NO_PROXY"]


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
