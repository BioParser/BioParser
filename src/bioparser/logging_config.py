"""
Produces one JSON object per line on stderr.

setup_logging runs on API startup (lifespan), and it puts a handler on the
root logger, so records from e.g. BioParser, uvicorn, httpx2 reach it and share the
same format.
"""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Literal, TextIO

type LogLevel = Literal["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"]

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
# NOTE: Do we need uvicorn.error: server-level messages (startup, shutdown,errors) ?
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.access")

# Every LogRecord's standard attribute, everything else goes to  extra={...}
_NOT_EXTRAS = frozenset(vars(logging.makeLogRecord({}))) | {"message", "asctime", "color_message"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
        }

        # TODO: make sure we didnt miss anything important from uvicorn.access
        if record.name == "uvicorn.access":
            payload.update(self._format_uvicorn_access(record))
        else:
            payload["msg"] = record.getMessage()

        # Add the custom fields to payload
        for key, value in vars(record).items():
            if key not in _NOT_EXTRAS:
                payload.setdefault(key, value)

        # If we call logger.exception() outside an except block
        if record.exc_info and record.exc_info[0] is not None:
            payload["exc_type"] = record.exc_info[0].__name__
            # Cache so a second handler does not format the traceback again
            if not record.exc_text:
                record.exc_text = self.formatException(record.exc_info)
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
            "path": path,
            "http_version": http_version,
            "status_code": status_code,
        }


def _json_default(value: object) -> str:
    # do not log bytes, just size
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<{memoryview(value).nbytes} bytes>"
    return str(value)


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
        root_logger.addHandler(stderr_handler)

    numeric_level = logging.getLevelNamesMapping()[level]
    logging.getLogger(APP_LOGGER_NAME).setLevel(numeric_level)
    # set a default level to loggers; overriden by explicit settings
    root_logger.setLevel(max(numeric_level, THIRD_PARTY_MIN_LEVEL))

    # remove uvicorns own handles and propagate the logs
    for name in _UVICORN_LOGGERS:
        uvicorn_logger = logging.getLogger(name)

        for uvicorn_handler in uvicorn_logger.handlers[:]:
            uvicorn_logger.removeHandler(uvicorn_handler)

        uvicorn_logger.propagate = True
