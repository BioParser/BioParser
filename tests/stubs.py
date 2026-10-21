"""Fake network hops"""

import json
import re
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx2

# What httpx2.MockTransport calls for every request a client sends
type Handler = Callable[[httpx2.Request], httpx2.Response]

# stub json one page and one text block for mineru mapper
STUB_JSON: dict[str, Any] = {
    "_backend": "pipeline",
    "_version_name": "3.4.5",
    "pdf_info": [
        {
            "page_idx": 0,
            "page_size": [595.0, 842.0],
            "para_blocks": [
                {
                    "type": "text",
                    "bbox": [10.0, 10.0, 500.0, 40.0],
                    "lines": [
                        {
                            "bbox": [10.0, 10.0, 500.0, 40.0],
                            "spans": [
                                {
                                    "type": "text",
                                    "bbox": [10.0, 10.0, 500.0, 40.0],
                                    "content": "Adult body mass averaged 12.5 g in the sampled population.",
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    ],
}


def file_parse_reply(middle: dict[str, Any] = STUB_JSON) -> tuple[int, dict[str, Any]]:
    """mineru-api's 200 reply to /v1/file_parse for one PDF."""
    entry = {"name": "a.pdf", "status": "completed", "content": {"middle_json": middle}}
    return 200, {"task_id": "job_1", "status": "completed", "files": [entry]}


@contextmanager
def fake_mineru(
    *replies: tuple[int, dict[str, Any]], health: Sequence[int] = ()
) -> Iterator[tuple[str, list[str]]]:
    """A mineru-api on loopback. Each POST gets the next reply, a DELETE gets 200, and
    GET /v1/health gets the next of ``health``, then 200.

    Yields the base URL and the "METHOD path?query" of every POST and DELETE it served.
    """
    pending = list(replies)
    probes = list(health)
    seen: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            self.rfile.read(int(self.headers["Content-Length"]))
            seen.append(f"POST {self.path}")
            self._reply(*pending.pop(0))

        def do_DELETE(self) -> None:
            seen.append(f"DELETE {self.path}")
            self._reply(200, {})

        def do_GET(self) -> None:
            status = probes.pop(0) if probes else 200
            self._reply(status, {"status": "healthy" if status == 200 else "starting"})

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", seen
    finally:
        server.shutdown()
        server.server_close()


class StubMinerUClient:
    """HTTP hop to mineru-api"""

    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.payload = payload if payload is not None else STUB_JSON
        self.calls: list[str] = []

    async def parse(self, *, filename: str, content: bytes) -> dict[str, Any]:
        self.calls.append(filename)
        return self.payload

    async def aclose(self) -> None:
        return None


class StubVLLMService:
    """HTTP hop to vLLM"""

    def __init__(self, observations: list[dict[str, Any]] | None = None) -> None:
        self.observations = observations
        self.prompts: list[str] = []
        self.system_prompts: list[str] = []
        self.up = True

    async def ping(self) -> bool:
        return self.up

    async def generate_json(
        self,
        prompt: str,
        *,
        schema: dict[str, Any],
        system_prompt: str,
        max_tokens: int,
    ) -> str:
        self.prompts.append(prompt)
        self.system_prompts.append(system_prompt)

        if self.observations is not None:
            return json.dumps({"observations": self.observations})

        match = re.search(r"\[([^\]]+)\]", prompt)
        ident = match.group(1) if match else "unknown"

        return json.dumps(
            {
                "observations": [
                    {
                        "taxon": "Mus musculus",  # kotihiiri
                        "trait": "body_mass",
                        "value": 12.5,
                        "unit": "g",
                        "evidence_block_id": ident,
                        "quotation": "12.5 g",
                    }
                ]
            }
        )

    async def aclose(self) -> None:
        return None
