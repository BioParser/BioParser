"""Fake network hops"""

import json
import re
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx2

from bioparser.jobqueue import JobQueueError, ParseJobMessage
from bioparser.jobstate import JobState, JobStateError
from bioparser.parser.models import (
    BlockKind,
    BlockRole,
    ContentBlock,
    Page,
    ParserArtifact,
    ParserInfo,
    TextContent,
)
from bioparser.storage import ArtifactMetadata, ArtifactNotFoundError, StorageError

# What httpx2.MockTransport calls for every request a client sends
type Handler = Callable[[httpx2.Request], httpx2.Response]

# stub json one page and one text block for mineru mapper
STUB_JSON: dict[str, Any] = {
    "schema": "docvortex.middle",
    "schema_version": "2.0",
    "metadata": {"file_suffix": "pdf", "producer": {"name": "mineru", "version": "4.0.12"}},
    "extensions": {
        "docvortex_layout": {
            "version": 1,
            "pages": [{"page_idx": 0, "width_pt": 595.0, "height_pt": 842.0}],
        }
    },
    "pages": [
        {
            "page_idx": 0,
            "blocks": [
                {
                    "type": "text",
                    "index": 0,
                    "bbox": [0.017, 0.012, 0.84, 0.048],
                    "content": [
                        {
                            "type": "text",
                            "content": "Adult body mass averaged 12.5 g in the sampled population.",
                        }
                    ],
                }
            ],
        }
    ],
    "is_full_document": True,
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


STUB_TEXT = "Adult body mass averaged 12.5 g in the sampled population."
# The quotation holds the taxon, the value and the unit once each, so the default extractor
# locates all of them. Use it with stub_parser_artifact(LOCATABLE_TEXT).
LOCATABLE_TEXT = "Mus musculus: adult body mass 12.5 g."
LOCATABLE_OBSERVATION: dict[str, Any] = {
    "taxon": "Mus musculus",
    "trait": "body_mass",
    "value": 12.5,
    "unit": "g",
    "evidence_block_id": "stub-p1-b0000",
    "quotation": "Mus musculus: adult body mass 12.5 g",
}


def stub_parser_artifact(text: str = STUB_TEXT) -> ParserArtifact:
    """A parser artifact with one page and one text block holding ``text``."""
    block = ContentBlock(
        block_id="stub-p1-b0000",
        order=0,
        kind=BlockKind.TEXT,
        role=BlockRole.BODY,
        content=TextContent(text=text),
    )
    return ParserArtifact(
        schema_version="1",
        checksum="0" * 64,
        parser=ParserInfo(name="default", version="1", configuration={}),
        pages=[Page(page_number=1, width=595.0, height=842.0, blocks=[block])],
    )


class StubParser:
    """A parser backend: returns ``artifact`` or raises ``error``. Records the PDF of each call."""

    def __init__(
        self,
        artifact: ParserArtifact | None = None,
        *,
        error: Exception | None = None,
        delay_s: float = 0.0,
    ) -> None:
        self.artifact = artifact if artifact is not None else stub_parser_artifact()
        self.error = error
        self.delay_s = delay_s
        self.pdfs: list[bytes] = []

    def parse(self, path: Path) -> ParserArtifact:
        self.pdfs.append(path.read_bytes())
        time.sleep(self.delay_s)  # stands in for minutes of real parsing
        if self.error is not None:
            raise self.error
        return self.artifact


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


class StubArtifactStorage:
    def __init__(self, events: list[str]) -> None:
        self.artifacts: dict[str, tuple[bytes, ArtifactMetadata]] = {}
        self.events = events
        self.store_error: StorageError | None = None
        self.retrieve_error: StorageError | None = None

    def store(
        self,
        content: bytes,
        metadata: ArtifactMetadata,
        *,
        overwrite: bool = False,
    ) -> str:
        self.events.append("artifact.store")
        if self.store_error is not None:
            raise self.store_error
        self.artifacts[metadata.artifact_path] = (content, metadata)
        return metadata.artifact_path

    def retrieve(self, artifact_path: str) -> bytes:
        self.events.append("artifact.retrieve")
        if self.retrieve_error is not None:
            raise self.retrieve_error
        try:
            return self.artifacts[artifact_path][0]
        except KeyError as exc:
            raise ArtifactNotFoundError(f"Artifact not found: {artifact_path}") from exc


class StubJobStateStore:
    def __init__(self, events: list[str]) -> None:
        self.states: dict[object, JobState] = {}
        self.events = events
        self.create_error: JobStateError | None = None
        self.get_error: JobStateError | None = None

    async def create(self, state: JobState) -> None:
        self.events.append("job_state.create")
        if self.create_error is not None:
            raise self.create_error
        self.states[str(state.job_id)] = state

    async def get(self, job_id: object) -> JobState | None:
        if self.get_error is not None:
            raise self.get_error
        self.events.append("job_state.get")
        return self.states.get(str(job_id))

    async def update(self, state: JobState) -> None:
        self.events.append("job_state.update")
        self.states[state.job_id] = state


class StubParseQueue:
    def __init__(self, events: list[str]) -> None:
        self.messages: list[ParseJobMessage] = []
        self.events = events
        self.submit_error: JobQueueError | None = None

    def submit(self, message: ParseJobMessage) -> None:
        self.events.append("queue.submit")
        if self.submit_error is not None:
            raise self.submit_error
        self.messages.append(message)


@dataclass
class InlinePipeline:
    """What /extract and run_pipeline() get. Replace either field to change it for the test."""

    parser: StubParser = field(default_factory=StubParser)
    vllm: StubVLLMService = field(default_factory=StubVLLMService)
    # (name, keyword arguments) of each get_parser() call
    parser_requests: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
