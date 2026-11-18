"""Fake network hops"""

import asyncio
import io
import json
import re
import threading
import zipfile
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, NamedTuple
from urllib.parse import unquote

import httpx2
from pydantic import BaseModel

from bioparser.db import documents as document_queries
from bioparser.db import engine as database
from bioparser.db import jobs as job_queries
from bioparser.db.documents import DocumentRecord
from bioparser.jobs import ALLOWED_TRANSITIONS, JobState, JobStatus
from bioparser.jobs.queue import JobQueueError, JobSubmitter, ParseJobMessage
from bioparser.parser.backend.mineru.schema import MINERU_VERSION
from bioparser.parser.models import (
    PARSER_ARTIFACT_SCHEMA_VERSION,
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
    "metadata": {"file_suffix": "pdf", "producer": {"name": "mineru", "version": MINERU_VERSION}},
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


def result_zip(middle: dict[str, Any] | None = STUB_JSON) -> bytes:
    """A mineru-api result ZIP as 4.2.3 packs output_formats=["default"]. ``None`` leaves out
    middle_json.json."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("markdown.md", "# Stub\n")
        if middle is not None:
            archive.writestr("middle_json.json", json.dumps(middle, ensure_ascii=False, indent=2))
        archive.writestr("structured_content.json", "{}")
        archive.writestr("images/", b"")
    return buffer.getvalue()


@dataclass(frozen=True)
class FakeTask:
    """How the fake mineru-api plays one task.

    ``refused`` answers POST /v1/tasks instead of creating the task. Otherwise each GET of the
    task shows the next of ``statuses``, the last one repeating. A completed file serves
    ``middle`` as its middle_json.json; a failed one carries ``error_code``. A cancelled task
    shows ``canceled`` from then on.
    """

    statuses: tuple[str, ...] = ("completed",)
    middle: dict[str, Any] = field(default_factory=lambda: STUB_JSON)
    error_code: str = "parse_failed"
    refused: tuple[int, dict[str, Any]] | None = None
    task_id: str = "job_1"


def parsed_task(middle: dict[str, Any] = STUB_JSON) -> FakeTask:
    """A task whose file completes with ``middle`` as its middle_json.json."""
    return FakeTask(middle=middle)


def failed_task(code: str) -> FakeTask:
    """A task whose file fails with the V1 error ``code``."""
    return FakeTask(statuses=("failed",), error_code=code)


def unfinished_task(status: str, *, task_id: str = "job_1") -> FakeTask:
    """A task whose file stays ``status`` (pending or running) until it is cancelled."""
    return FakeTask(statuses=(status,), task_id=task_id)


def refused_task(status: int, body: dict[str, Any]) -> FakeTask:
    """POST /v1/tasks answers ``status`` and ``body``; no task is created."""
    return FakeTask(refused=(status, body))


class FakeMinerU(NamedTuple):
    url: str
    # "METHOD path" of every request but the health probes, in the order they came
    seen: list[str]
    # The JSON body of every POST /v1/tasks
    created: list[dict[str, Any]]


@dataclass
class _LiveTask:
    plan: FakeTask
    file_name: str
    polls: int = 0
    cancelled: bool = False

    @property
    def file_id(self) -> str:
        return f"file-{self.plan.task_id}"


@contextmanager
def fake_mineru(*tasks: FakeTask, health: Sequence[int] = ()) -> Iterator[FakeMinerU]:
    """mineru-api's Tasks API (MinerU 4.2) on loopback. Each POST /v1/tasks plays the next of
    ``tasks``, and GET /v1/health answers the next of ``health``, then 200."""
    pending = list(tasks)
    probes = list(health)
    seen: list[str] = []
    created: list[dict[str, Any]] = []
    live: dict[str, _LiveTask] = {}
    origin: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, payload: dict[str, Any]) -> None:
            self._reply(status, json.dumps(payload).encode(), "application/json")

        def _not_found(self) -> None:
            self._json(404, {"error": {"code": "task_not_found", "message": "secret text"}})

        def _request_body(self) -> bytes:
            return self.rfile.read(int(self.headers.get("Content-Length", 0)))

        def _file(self, task: _LiveTask, status: str) -> dict[str, Any]:
            file: dict[str, Any] = {
                "file_id": task.file_id,
                "file_name": task.file_name,
                "status": status,
            }
            if status == "completed":
                file["result_url"] = f"{origin[0]}/v1/files/{task.file_id}/content"
            if status == "failed":
                file["error"] = {"code": task.plan.error_code, "message": "secret text"}
            return file

        def do_POST(self) -> None:
            body = self._request_body()
            seen.append(f"POST {self.path}")
            if self.path == "/v1/tasks":
                request = json.loads(body)
                created.append(request)
                plan = pending.pop(0)
                if plan.refused is not None:
                    self._json(*plan.refused)
                    return
                task = _LiveTask(plan, request["files"][0]["file_name"])
                live[plan.task_id] = task
                upload = {
                    "url": f"{origin[0]}/v1/tasks/{plan.task_id}/uploads/{task.file_id}",
                    "headers": {"Content-Type": "application/octet-stream"},
                    "expires_at": 1,
                }
                file = self._file(task, "waiting-file") | {"upload": upload}
                self._json(
                    202, {"task_id": plan.task_id, "status": "waiting-file", "files": [file]}
                )
                return
            match = re.fullmatch(r"/v1/tasks/([^/]+)/cancel", self.path)
            if match is None or (cancelled := live.get(unquote(match[1]))) is None:
                self._not_found()
                return
            cancelled.cancelled = True
            payload = {
                "task_id": cancelled.plan.task_id,
                "status": "canceled",
                "finished_at": "2026-01-01T00:00:00Z",
                "files": [self._file(cancelled, "canceled")],
            }
            self._json(200, payload)

        def do_PUT(self) -> None:
            self._request_body()
            seen.append(f"PUT {self.path}")
            match = re.fullmatch(r"/v1/tasks/([^/]+)/uploads/([^/]+)", self.path)
            if match is None or (task := live.get(unquote(match[1]))) is None:
                self._not_found()
                return
            self._json(
                200, {"task_id": task.plan.task_id, "file_id": task.file_id, "status": "pending"}
            )

        def do_GET(self) -> None:
            if self.path == "/v1/health":
                probe = probes.pop(0) if probes else 200
                self._json(probe, {"status": "ok" if probe == 200 else "starting"})
                return
            seen.append(f"GET {self.path}")
            if match := re.fullmatch(r"/v1/files/file-([^/]+)/content", self.path):
                done = live.get(unquote(match[1]))
                if done is None:
                    self._not_found()
                    return
                self._reply(200, result_zip(done.plan.middle), "application/zip")
                return
            match = re.fullmatch(r"/v1/tasks/([^/]+)", self.path)
            if match is None or (task := live.get(unquote(match[1]))) is None:
                self._not_found()
                return
            statuses = task.plan.statuses
            status = "canceled" if task.cancelled else statuses[min(task.polls, len(statuses) - 1)]
            task.polls += 1
            payload = {
                "task_id": task.plan.task_id,
                "status": status,
                "created_at": "2026-01-01T00:00:00Z",
                "output_formats": ["default"],
                "progress": {"completed": 0, "failed": 0, "total": 1},
                "files": [self._file(task, status)],
            }
            self._json(200, payload)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    origin.append(f"http://127.0.0.1:{server.server_address[1]}")
    # shutdown() waits for the next poll; the default 0.5 s would add that much to every test.
    threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    ).start()
    try:
        yield FakeMinerU(origin[0], seen, created)
    finally:
        server.shutdown()
        server.server_close()


STUB_TEXT = "Adult body mass averaged 12.5 g in the sampled population."
# The quotation holds the taxon, the value and the unit once each, so the default extractor
# locates all of them. Use it with stub_parser_artifact(LOCATABLE_TEXT).
LOCATABLE_TEXT = "Mus musculus: adult body mass 12.5 g."
LOCATABLE_OBSERVATION: dict[str, Any] = {
    "taxon": "Mus musculus",
    "trait_code": "body_mass",
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
        schema_version=PARSER_ARTIFACT_SCHEMA_VERSION,
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

    async def generate_model(
        self,
        prompt: str,
        response_model: type[BaseModel],
        *,
        system_prompt: str,
        max_tokens: int | None = None,
    ) -> BaseModel:
        self.prompts.append(prompt)
        self.system_prompts.append(system_prompt)

        if self.observations is not None:
            payload = {"observations": self.observations}
        else:
            match = re.search(r"\[([^\]]+)\]", prompt)
            ident = match.group(1) if match else "unknown"
            payload = {
                "observations": [
                    {
                        "taxon": "Mus musculus",  # kotihiiri
                        "trait_code": "body_mass",
                        "value": 12.5,
                        "unit": "g",
                        "evidence_block_id": ident,
                        "quotation": "12.5 g",
                    }
                ]
            }
        return response_model.model_validate(payload)

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
        if metadata.size_bytes is None:
            metadata = metadata.model_copy(update={"size_bytes": len(content)})
        if metadata.checksum is None:
            from bioparser.hashing import sha256_bytes

            metadata = metadata.model_copy(update={"checksum": sha256_bytes(content)})
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

    def retrieve_metadata(self, artifact_path: str) -> ArtifactMetadata:
        self.events.append("artifact.retrieve_metadata")
        if self.retrieve_error is not None:
            raise self.retrieve_error
        try:
            return self.artifacts[artifact_path][1]
        except KeyError as exc:
            raise ArtifactNotFoundError(f"Artifact not found: {artifact_path}") from exc

    def retrieve_artifact(self, artifact_path: str) -> StoredArtifact:
        self.events.append("artifact.retrieve_artifact")
        if self.retrieve_error is not None:
            raise self.retrieve_error
        try:
            content, meta = self.artifacts[artifact_path]
            return StoredArtifact(content=content, metadata=meta)
        except KeyError as exc:
            raise ArtifactNotFoundError(f"Artifact not found: {artifact_path}") from exc

    def exists(self, artifact_path: str) -> bool:
        self.events.append("artifact.exists")
        return artifact_path in self.artifacts


_jobs: list["StubJobs"] = []


class StubJobs:
    """One in-memory jobs and documents table for the API tests and both worker handler tests."""

    def __init__(self, state: JobState | None = None, *, events: list[str] | None = None) -> None:
        _jobs.append(self)
        self.states: dict[str, JobState] = {}
        if state is not None:
            self.states[str(state.job_id)] = state
        self.documents: dict[str, DocumentRecord] = {}
        self.events = events if events is not None else []
        self.create_error: Exception | None = None
        self.get_error: Exception | None = None
        self.claim_error: Exception | None = None
        self.doc_create_error: Exception | None = None
        self.doc_get_error: Exception | None = None

    @property
    def state(self) -> JobState | None:
        if not self.states:
            return None
        return next(iter(self.states.values()))

    async def create(self, connection: object, state: JobState) -> JobState:
        self.events.append("job_state.create")
        if self.create_error is not None:
            raise self.create_error
        self.states[str(state.job_id)] = state
        return state

    async def get(self, connection: object, job_id: object) -> JobState | None:
        if self.get_error is not None:
            raise self.get_error
        self.events.append("job_state.get")
        return self.states.get(str(job_id))

    def _name(self, state: JobState) -> str | None:
        document = self.documents.get(state.document_checksum)
        if document is None:
            return None
        return document.display_name

    async def claim(
        self,
        connection: object,
        job_id: object,
        *,
        status: JobStatus,
        from_statuses: Collection[str],
        max_claims: int,
    ) -> str:
        if self.claim_error is not None:
            raise self.claim_error
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

    async def list_jobs(
        self,
        connection: object,
        *,
        job_id: object = None,
        status: JobStatus | None = None,
        parser: str | None = None,
    ) -> list[tuple[JobState, str | None]]:
        self.events.append("job_state.list")
        if self.get_error is not None:
            raise self.get_error
        if self.doc_get_error is not None:
            raise self.doc_get_error
        jobs = list(self.states.values())
        if job_id is not None:
            jobs = [j for j in jobs if str(j.job_id) == str(job_id)]
        if status is not None:
            jobs = [j for j in jobs if j.status == status]
        if parser is not None:
            jobs = [j for j in jobs if j.parser == parser]
        return [(job, self._name(job)) for job in jobs]

    async def list_jobs_for_document(
        self,
        connection: object,
        checksum: str,
    ) -> list[tuple[JobState, str | None]] | None:
        self.events.append("job_state.list_for_document")
        if self.get_error is not None:
            raise self.get_error
        if checksum not in self.documents:
            return None
        return [
            (job, self._name(job))
            for job in self.states.values()
            if job.document_checksum == checksum
        ]

    async def get_document(self, connection: object, checksum: str) -> DocumentRecord | None:
        self.events.append("document.get")
        if self.doc_get_error is not None:
            raise self.doc_get_error
        return self.documents.get(checksum)

    async def create_document(
        self,
        connection: object,
        *,
        checksum: str,
        display_name: str | None,
        size_bytes: int,
    ) -> tuple[DocumentRecord, bool]:
        self.events.append("document.create")
        if self.doc_create_error is not None:
            raise self.doc_create_error
        if checksum in self.documents:
            return self.documents[checksum], False
        record = DocumentRecord(
            checksum=checksum,
            display_name=display_name,
            size_bytes=size_bytes,
            created_at=datetime.now(UTC),
        )
        self.documents[checksum] = record
        return record, True

    async def list_documents(self, connection: object) -> list[DocumentRecord]:
        self.events.append("document.list")
        if self.doc_get_error is not None:
            raise self.doc_get_error
        docs = list(self.documents.values())
        docs.sort(key=lambda d: d.created_at, reverse=True)
        return docs


def bind_jobs(monkeypatch: Any) -> None:
    """Point job queries and the pool at the jobs table most recently constructed."""
    tables = _jobs

    async def get(connection: object, job_id: object) -> JobState | None:
        return await tables[-1].get(connection, job_id)

    async def create(connection: object, state: JobState) -> JobState:
        return await tables[-1].create(connection, state)

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

    async def list_jobs(
        connection: object,
        *,
        job_id: object = None,
        status: JobStatus | None = None,
        parser: str | None = None,
    ) -> list[tuple[JobState, str | None]]:
        return await tables[-1].list_jobs(connection, job_id=job_id, status=status, parser=parser)

    async def list_jobs_for_document(
        connection: object,
        checksum: str,
    ) -> list[tuple[JobState, str | None]] | None:
        return await tables[-1].list_jobs_for_document(connection, checksum)

    async def get_document(connection: object, checksum: str) -> DocumentRecord | None:
        return await tables[-1].get_document(connection, checksum)

    async def create_document(
        connection: object,
        *,
        checksum: str,
        display_name: str | None,
        size_bytes: int,
    ) -> tuple[DocumentRecord, bool]:
        return await tables[-1].create_document(
            connection, checksum=checksum, display_name=display_name, size_bytes=size_bytes
        )

    async def list_documents(connection: object) -> list[DocumentRecord]:
        return await tables[-1].list_documents(connection)

    async def open_pool(url: str) -> None:
        return None

    async def close_pool() -> None:
        return None

    @asynccontextmanager
    async def _transaction():  # type: ignore[no-untyped-def]
        jobs = tables[-1]
        saved_states = dict(jobs.states)
        saved_documents = dict(jobs.documents)
        try:
            yield object()
        except BaseException:
            jobs.states.clear()
            jobs.states.update(saved_states)
            jobs.documents.clear()
            jobs.documents.update(saved_documents)
            raise

    monkeypatch.setattr(job_queries, "get", get)
    monkeypatch.setattr(job_queries, "create", create)
    monkeypatch.setattr(job_queries, "claim", claim)
    monkeypatch.setattr(job_queries, "record_parsed", record_parsed)
    monkeypatch.setattr(job_queries, "record_done", record_done)
    monkeypatch.setattr(job_queries, "mark_failed", mark_failed)
    monkeypatch.setattr(job_queries, "list_jobs", list_jobs)
    monkeypatch.setattr(job_queries, "list_jobs_for_document", list_jobs_for_document)
    monkeypatch.setattr(document_queries, "get", get_document)
    monkeypatch.setattr(document_queries, "create", create_document)
    monkeypatch.setattr(document_queries, "list_documents", list_documents)
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
