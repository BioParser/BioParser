"""
Produces one JSON object per line on stderr.

setup_logging runs on API startup (lifespan), and it puts a handler on the
root logger, so records from e.g. BioParser, uvicorn, httpx2 reach it and share the
same format.

log_context() adds fields, such as the upload checksum, to every record logged
inside it, whichever logger created the record.
"""

import json
import logging
import sys
import traceback
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from time import perf_counter
from types import TracebackType
from typing import Literal, TextIO
from urllib.parse import urlsplit, urlunsplit

type LogLevel = Literal["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"]

_CAUSE = "\nThe above exception was the direct cause of the following exception:\n\n"
_CONTEXT = "\nDuring handling of the above exception, another exception occurred:\n\n"

HANDLER_NAME = "bioparser"
APP_LOGGER_NAME = "bioparser"

# third-party loggers should at least output WARNINGS as minimum
THIRD_PARTY_MIN_LEVEL = logging.WARNING

# uvicorns LOGGING_CONFIG gives these handlers, and their levels are set
# by uvicorn's --log-level (default=INFO)
# https://uvicorn.dev/concepts/logging/
#
# uvicorn: Parent logger (rarely used directly)
# uvicorn.access: Per-request access log lines
# uvicorn.error (startup, shutdown, errors) needs no entry: it has no handler of
# its own and propagates through "uvicorn".
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.access")

# Every LogRecord's standard attribute, everything else goes to  extra={...}
_NOT_EXTRAS = frozenset(vars(logging.makeLogRecord({}))) | {"message", "asctime", "color_message"}

# Fields that log_context() adds to every record logged inside it
_context: ContextVar[dict[str, object] | None] = ContextVar("bioparser_log_context", default=None)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
        }

        # uvicorn's h11, httptools and zttp protocols all log the same five args.
        # Any other shape keeps its message rather than becoming an empty line.
        access = self._format_uvicorn_access(record) if record.name == "uvicorn.access" else {}
        if access:
            payload.update(access)
        else:
            payload["msg"] = record.getMessage()

        # Add the custom fields to payload
        for key, value in vars(record).items():
            if key not in _NOT_EXTRAS:
                payload.setdefault(key, value)

        # exc is None when logger.exception() runs outside an except block
        if record.exc_info and (exc := record.exc_info[1]) is not None:
            payload["exc_type"] = type(exc).__name__
            record.exc_text = _redacted_traceback(exc, record.exc_info[2])
            payload["exc_info"] = record.exc_text

        if record.stack_info:
            payload["stack_info"] = self.formatStack(record.stack_info)

        return json.dumps(payload, default=_json_default)

    @staticmethod
    def _format_uvicorn_access(record: logging.LogRecord) -> dict[str, object]:
        args = record.args

        if not isinstance(args, tuple) or len(args) != 5:
            return {}

        client_addr, method, path, http_version, status_code = args

        return {
            "client_addr": client_addr,
            "method": method,
            "path": str(path).partition("?")[0],
            "http_version": http_version,
            "status_code": status_code,
        }


def _redacted_traceback(exc: BaseException, tb: TracebackType | None) -> str:
    blocks: list[str] = []

    link: traceback.TracebackException | None = traceback.TracebackException(type(exc), exc, tb)
    while link is not None:
        block = ""
        if link.stack:
            block = "Traceback (most recent call last):\n" + "".join(link.stack.format())
        name = link.exc_type_str
        only = "".join(link.format_exception_only())
        if name.startswith("bioparser.") or only == f"{name}\n":
            block += only
        else:
            block += f"{name}: [message redacted]\n"
        if link.__cause__ is not None:
            blocks.append(_CAUSE + block)
            link = link.__cause__
        elif link.__context__ is not None and not link.__suppress_context__:
            blocks.append(_CONTEXT + block)
            link = link.__context__
        else:
            blocks.append(block)
            link = None
    return "".join(reversed(blocks)).removesuffix("\n")


def _json_default(value: object) -> str:
    # do not log bytes, just size
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<{memoryview(value).nbytes} bytes>"
    return str(value)


class _ContextFilter(logging.Filter):
    """Copies the log_context() fields onto each record.

    On the handler, not a logger: a logger's filters do not run for records
    created by its child loggers, so uvicorn, httpx2 and openai records would
    miss the fields.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in (_context.get() or {}).items():
            record.__dict__.setdefault(key, value)  # an explicit extra= wins
        return True


class _StderrHandler(logging.StreamHandler[TextIO]):
    """
    StreamHandler writes to what sys.stderr is pointed at
    """

    def __init__(self) -> None:
        logging.Handler.__init__(self)

    @property
    def stream(self) -> TextIO:  # type: ignore[override] # read-only
        return sys.stderr


def setup_logging(level: LogLevel) -> None:
    """
    Send every logger's record to a JSON handler on stderr.

    Idempotent: lifespan runs on startup of the app object (app.py)

    Level: applies to BioParser logger tree. Third-party loggers should stay at
           minimum THIRD_PARTY_LEVEL, and uvicorn's loggers keep their own
           level.
    """
    root_logger = logging.getLogger()
    if not any(handler.name == HANDLER_NAME for handler in root_logger.handlers):
        stderr_handler = _StderrHandler()
        stderr_handler.name = HANDLER_NAME
        stderr_handler.setFormatter(JsonFormatter())
        stderr_handler.addFilter(_ContextFilter())
        root_logger.addHandler(stderr_handler)

    numeric_level = logging.getLevelNamesMapping()[level]
    logging.getLogger(APP_LOGGER_NAME).setLevel(numeric_level)
    # set a default level to loggers; overriden by explicit settings
    root_logger.setLevel(max(numeric_level, THIRD_PARTY_MIN_LEVEL))

    # remove uvicorns own handles and propagate the logs
    for name in _UVICORN_LOGGERS:
        uvicorn_logger = logging.getLogger(name)
        # No handlers: uvicorn never configured this logger (it already
        # propagates), or --no-access-log turned it off (handlers=[] and
        # propagate=False). uvicorn checks hasHandlers() per connection, so
        # propagating would switch the access log back on.
        if not uvicorn_logger.handlers:
            continue

        for uvicorn_handler in uvicorn_logger.handlers[:]:
            uvicorn_logger.removeHandler(uvicorn_handler)

        uvicorn_logger.propagate = True


@contextmanager
def log_context(**fields: object) -> Iterator[None]:
    token = _context.set({**(_context.get() or {}), **fields})
    try:
        yield
    finally:
        _context.reset(token)


def error_fields(exc: BaseException) -> dict[str, str]:
    fields = {"error": type(exc).__name__}
    if exc.__cause__ is not None:
        fields["cause"] = type(exc.__cause__).__name__
    return fields


def redact_url(url: str) -> str:
    """url for a log: user:password@ becomes `***@`, query and fragment go."""
    if "://" not in url and not url.startswith("//"):
        url = f"//{url}"
    try:
        parts = urlsplit(url)
    except ValueError:
        return "***"
    _userinfo, at, hostport = parts.netloc.rpartition("@")
    netloc = f"***@{hostport}" if at else hostport
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def stopwatch() -> Callable[[], float]:
    """Start timing; the returned function gives the milliseconds since, for log fields."""
    started = perf_counter()
    return lambda: round((perf_counter() - started) * 1000, 1)
