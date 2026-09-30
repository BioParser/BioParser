"""Process liveness for the parser worker.

A separate thread serves GET /health. A long parse does not block it. If the
process is wedged badly enough that this thread cannot answer, the probe fails
and the platform can restart the container.
"""

from __future__ import annotations

import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

_HEALTH_PATH = "/health"
_HEALTH_BODY = b'{"status":"ok"}'


class _HealthHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        if urlparse(self.path).path != _HEALTH_PATH:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(_HEALTH_BODY)))
        self.end_headers()
        self.wfile.write(_HEALTH_BODY)

    def log_message(self, format: str, *args: object) -> None:
        return


class LivenessServer:
    """HTTP liveness probe. `port` 0 asks the OS for a free port."""

    def __init__(self, port: int) -> None:
        self._port = port
        self._httpd: ThreadingHTTPServer | None = None

    @property
    def port(self) -> int:
        return self._port

    def start(self) -> None:
        httpd = ThreadingHTTPServer(("0.0.0.0", self._port), _HealthHandler)
        self._httpd = httpd
        self._port = int(httpd.server_address[1])
        threading.Thread(target=httpd.serve_forever, name="liveness", daemon=True).start()

    def close(self) -> None:
        httpd = self._httpd
        if httpd is None:
            return
        self._httpd = None
        httpd.shutdown()
        httpd.server_close()
