"""Fake network hops"""

import asyncio
import json
import re
import threading
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx2

from bioparser.db import engine as database
from bioparser.db import jobs as job_queries
from bioparser.jobqueue import JobQueueError, JobSubmitter, ParseJobMessage
from bioparser.jobstate import ALLOWED_TRANSITIONS, JobState, JobStatus
from bioparser.parser.models import (
    BlockKind,
    BlockRole,
    ContentBlock,
    Page,
    ParserArtifact,
    ParserInfo,
    TextContent,
)
from bioparser.storage import (
    ArtifactAlreadyExistsError,
    ArtifactMetadata,
    ArtifactNotFoundError,
    StorageError,
    StoredArtifact,
)

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

    async def parse(self, path: Path) -> ParserArtifact:
        self.pdfs.append(path.read_bytes())
        await asyncio.sleep(self.delay_s)  # stands in for minutes of real parsing
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


class MemoryStorage:
    """In-memory artifacts for the parse and extract handler tests.

    `payloads` can already hold files the handler reads. A later `store` of the
    same path raises, the same way the filesystem store does.
    """

    def __init__(self, payloads: Mapping[str, bytes] | None = None) -> None:
        self.payloads: dict[str, bytes] = dict(payloads or {})
        self.metadata: dict[str, ArtifactMetadata] = {}
        self.stores = 0

    async def store(self, content: bytes, metadata: ArtifactMetadata) -> str:
        if metadata.artifact_path in self.payloads:
            raise ArtifactAlreadyExistsError(metadata.artifact_path)
        self.payloads[metadata.artifact_path] = content
        self.metadata[metadata.artifact_path] = metadata
        self.stores += 1
        return metadata.artifact_path

    async def retrieve(self, artifact_path: str) -> bytes:
        try:
            return self.payloads[artifact_path]
        except KeyError as exc:
            raise ArtifactNotFoundError(artifact_path) from exc

    async def exists(self, artifact_path: str) -> bool:
        return artifact_path in self.payloads

    async def retrieve_metadata(self, artifact_path: str) -> ArtifactMetadata:
        return self.metadata[artifact_path]

    async def retrieve_artifact(self, artifact_path: str) -> StoredArtifact:
        raise NotImplementedError


class StubArtifactStorage:
    def __init__(self, events: list[str]) -> None:
        self.artifacts: dict[str, tuple[bytes, ArtifactMetadata]] = {}
        self.events = events
        self.store_error: StorageError | None = None
        self.retrieve_error: StorageError | None = None

    async def store(
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

    async def retrieve(self, artifact_path: str) -> bytes:
        self.events.append("artifact.retrieve")
        if self.retrieve_error is not None:
            raise self.retrieve_error
        try:
            return self.artifacts[artifact_path][0]
        except KeyError as exc:
            raise ArtifactNotFoundError(f"Artifact not found: {artifact_path}") from exc


_jobs: list["StubJobs"] = []


class StubJobs:
    """One in-memory jobs table for the API tests and both worker handler tests."""

    def __init__(self, state: JobState | None = None, *, events: list[str] | None = None) -> None:
        _jobs.append(self)
        self.states: dict[str, JobState] = {}
        if state is not None:
            self.states[str(state.job_id)] = state
        self.events = events if events is not None else []
        self.create_error: Exception | None = None
        self.get_error: Exception | None = None

    @property
    def state(self) -> JobState | None:
        if not self.states:
            return None
        return next(iter(self.states.values()))

    async def create(self, connection: object, state: JobState) -> None:
        self.events.append("job_state.create")
        if self.create_error is not None:
            raise self.create_error
        self.states[str(state.job_id)] = state

    async def get(self, connection: object, job_id: object) -> JobState | None:
        if self.get_error is not None:
            raise self.get_error
        self.events.append("job_state.get")
        return self.states.get(str(job_id))

    async def claim(
        self,
        connection: object,
        job_id: object,
        *,
        status: JobStatus,
        from_statuses: Collection[str],
        max_claims: int,
    ) -> str:
        state = self.states.get(str(job_id))
        if state is None:
            return "missing"
        if state.status not in from_statuses:
            return "skipped"
        if state.claims(status) >= max_claims:
            if "failed" not in ALLOWED_TRANSITIONS[state.status]:
                return "skipped"
            self.states[str(job_id)] = state.with_changes(
                status="failed", error_code="internal_error"
            )
            return "exhausted"
        if status == "parsing":
            claimed = state.with_changes(status=status, parsing_claims=state.parsing_claims + 1)
        else:
            claimed = state.with_changes(
                status=status, extracting_claims=state.extracting_claims + 1
            )
        self.states[str(job_id)] = claimed
        return "claimed"

    async def record_parsed(self, connection: object, job_id: object, artifact_path: str) -> bool:
        state = self.states.get(str(job_id))
        if state is None or state.status != "parsing":
            return False
        self.states[str(job_id)] = state.with_changes(
            status="parsed", parse_result_ref=artifact_path
        )
        return True

    async def record_done(self, connection: object, job_id: object, artifact_path: str) -> bool:
        state = self.states.get(str(job_id))
        if state is None or state.status != "extracting":
            return False
        self.states[str(job_id)] = state.with_changes(
            status="done", extract_result_ref=artifact_path
        )
        return True

    async def mark_failed(
        self,
        connection: object,
        job_id: object,
        code: str,
        *,
        from_statuses: Collection[str],
    ) -> str:
        self.events.append("job_state.mark_failed")
        state = self.states.get(str(job_id))
        if state is None:
            return "missing"
        if state.status not in from_statuses:
            return "skipped"
        if "failed" not in ALLOWED_TRANSITIONS[state.status]:
            return "not_allowed"
        self.states[str(job_id)] = state.with_changes(status="failed", error_code=code)
        return "recorded"


def bind_jobs(monkeypatch: Any) -> None:
    """Point job queries and the pool at the jobs table most recently constructed."""
    tables = _jobs

    async def get(connection: object, job_id: object) -> JobState | None:
        return await tables[-1].get(connection, job_id)

    async def create(connection: object, state: JobState) -> None:
        await tables[-1].create(connection, state)

    async def claim(
        connection: object,
        job_id: object,
        *,
        status: JobStatus,
        from_statuses: Collection[str],
        max_claims: int,
    ) -> str:
        return await tables[-1].claim(
            connection,
            job_id,
            status=status,
            from_statuses=from_statuses,
            max_claims=max_claims,
        )

    async def record_parsed(connection: object, job_id: object, artifact_path: str) -> bool:
        return await tables[-1].record_parsed(connection, job_id, artifact_path)

    async def record_done(connection: object, job_id: object, artifact_path: str) -> bool:
        return await tables[-1].record_done(connection, job_id, artifact_path)

    async def mark_failed(
        connection: object,
        job_id: object,
        code: str,
        *,
        from_statuses: Collection[str],
    ) -> str:
        return await tables[-1].mark_failed(connection, job_id, code, from_statuses=from_statuses)

    async def open_pool(url: str) -> None:
        return None

    async def close_pool() -> None:
        return None

    @asynccontextmanager
    async def _transaction():  # type: ignore[no-untyped-def]
        yield object()

    monkeypatch.setattr(job_queries, "get", get)
    monkeypatch.setattr(job_queries, "create", create)
    monkeypatch.setattr(job_queries, "claim", claim)
    monkeypatch.setattr(job_queries, "record_parsed", record_parsed)
    monkeypatch.setattr(job_queries, "record_done", record_done)
    monkeypatch.setattr(job_queries, "mark_failed", mark_failed)
    monkeypatch.setattr(database, "open", open_pool)
    monkeypatch.setattr(database, "aclose", close_pool)
    monkeypatch.setattr(database, "transaction", _transaction)


def clear_jobs() -> None:
    _jobs.clear()


class StubParseQueue(JobSubmitter[ParseJobMessage]):
    def __init__(self, events: list[str]) -> None:
        self.messages: list[ParseJobMessage] = []
        self.events = events
        self.submit_error: JobQueueError | None = None

    async def submit(self, message: ParseJobMessage) -> None:
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
