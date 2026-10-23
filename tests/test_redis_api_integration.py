import asyncio
import hashlib
import json
import os
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from uuid import UUID

import httpx2
import pytest
from httpx2 import ASGITransport

from bioparser.api.app import app
from bioparser.extractor_names import DEFAULT_EXTRACTOR_NAME
from bioparser.jobqueue import ParseJobMessage
from bioparser.jobqueue.dramatiq import DramatiqJobQueue
from bioparser.jobqueue.dramatiq.redis_queue import _create_redis_broker
from bioparser.jobstate import JobState, RedisJobStateStore
from bioparser.parser import ParserArtifact
from bioparser.parser.models import ParserInfo
from bioparser.parser_names import DEFAULT_PARSER_NAME
from bioparser.storage import ArtifactMetadata, FileSystemArtifactStorage

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

TEST_REDIS_URL = os.environ.get("BIOPARSER_TEST_REDIS_URL", "redis://localhost:6379/15")
MINIMAL_PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _Recorder:
    def __init__(self) -> None:
        self.received: list[ParseJobMessage] = []

    def handle(self, message: ParseJobMessage) -> None:
        self.received.append(message)

    def on_malformed(self, payload: object) -> None:
        pytest.fail(f"queue produced a malformed payload: {payload!r}")

    def on_failed(self, message: ParseJobMessage, exc: BaseException) -> None:
        pytest.fail(f"queue delivery failed for {message.job_id}: {exc}")


@pytest.fixture
async def redis_api_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> AsyncGenerator[
    tuple[
        RedisJobStateStore,
        DramatiqJobQueue[ParseJobMessage],
        FileSystemArtifactStorage,
    ]
]:
    # Isolate both Redis adapters per test without flushing a shared database.
    unique_id = uuid.uuid4().hex
    state_prefix = f"bioparser:test:api:{unique_id}:"
    broker_namespace = f"bioparser-test-api-{unique_id}"
    queue_name = f"parse_{unique_id}"

    job_store = RedisJobStateStore(
        TEST_REDIS_URL,
        key_prefix=state_prefix,
        ttl_seconds=60,
    )
    try:
        await job_store._redis.ping()
    except Exception as exc:  # noqa: BLE001 - any connection failure means "skip"
        await job_store.aclose()
        pytest.skip(f"no Redis available at {TEST_REDIS_URL}: {exc}")

    broker = _create_redis_broker(TEST_REDIS_URL, namespace=broker_namespace)
    parse_queue = DramatiqJobQueue(
        name=queue_name,
        model=ParseJobMessage,
        broker=broker,
        worker_timeout_ms=50,
    )
    artifact_storage = FileSystemArtifactStorage(tmp_path / "artifacts")

    # ASGITransport does not run the production lifespan here, so inject the
    # same concrete adapters that lifespan would create.
    monkeypatch.setattr(app.state, "job_store", job_store, raising=False)
    monkeypatch.setattr(app.state, "parse_queue", parse_queue, raising=False)
    monkeypatch.setattr(app.state, "artifact_storage", artifact_storage, raising=False)

    try:
        yield job_store, parse_queue, artifact_storage
    finally:
        # Remove only this test's queue namespace and state prefix. Redis may
        # be shared with other tests or a developer's local environment.
        broker.flush_all()
        broker.client.delete(broker_namespace)
        broker.client.close()
        keys = [key async for key in job_store._redis.scan_iter(match=f"{state_prefix}*")]
        if keys:
            await job_store._redis.delete(*keys)
        await job_store.aclose()


async def test_redis_backed_submission_and_polling(
    redis_api_dependencies: tuple[
        RedisJobStateStore,
        DramatiqJobQueue[ParseJobMessage],
        FileSystemArtifactStorage,
    ],
) -> None:
    """Exercise submission and polling across the real Redis adapter boundary."""
    job_store, parse_queue, artifact_storage = redis_api_dependencies

    async with httpx2.AsyncClient(
        transport=ASGITransport(app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/extractions",
            files={"file": ("sample.pdf", MINIMAL_PDF, "application/pdf")},
        )

        assert response.status_code == 202
        submission = response.json()
        job_id = submission["job_id"]
        assert submission == {"job_id": job_id, "status": "queued"}

        expected_pdf_ref = f"pdf/{hashlib.sha256(MINIMAL_PDF).hexdigest()}"
        parsed_job_id = UUID(job_id)
        queued = await job_store.get(parsed_job_id)
        assert queued is not None
        assert queued == JobState(
            job_id=parsed_job_id,
            status="queued",
            parser=DEFAULT_PARSER_NAME,
            extractor=DEFAULT_EXTRACTOR_NAME,
            pdf_ref=expected_pdf_ref,
        )
        assert artifact_storage.retrieve(queued.pdf_ref) == MINIMAL_PDF

        # Redis must contain only typed state and artifact references, never
        # the uploaded PDF payload itself.
        raw_state = await job_store._redis.get(job_store._key(parsed_job_id))
        assert raw_state is not None
        assert MINIMAL_PDF not in raw_state
        assert json.loads(raw_state) == queued.model_dump(mode="json")

        poll = await client.get(f"/api/jobs/{job_id}")
        assert poll.status_code == 200
        assert poll.json() == submission

        # Drain the real Redis-backed Dramatiq queue to verify the serialized
        # message contract, rather than inspecting an in-memory fake.
        recorder = _Recorder()
        await asyncio.to_thread(parse_queue.consume, recorder, until_empty=True)
        assert recorder.received == [
            ParseJobMessage(
                job_id=queued.job_id,
            )
        ]

        # Worker execution is outside this issue, so simulate only its state
        # transitions through the real store and verify the public mappings.
        running = queued.with_claim("parsing")
        await job_store.update(running)
        poll = await client.get(f"/api/jobs/{job_id}")
        assert poll.status_code == 200
        assert poll.json() == {"job_id": job_id, "status": "parsing"}

        succeeded = running.with_changes(
            status="parsed",
            parse_result_ref=f"art-result-{job_id}",
        )
        parse_ref = succeeded.parse_result_ref
        assert parse_ref is not None

        artifact_storage.store(
            ParserArtifact(
                schema_version="1",
                checksum=hashlib.sha256(MINIMAL_PDF).hexdigest(),
                parser=ParserInfo(name="test", version="1", configuration={}),
                pages=[],
            )
            .model_dump_json()
            .encode(),
            ArtifactMetadata(
                artifact_path=parse_ref,
                media_type="application/json",
            ),
        )
        await job_store.update(succeeded)
        poll = await client.get(f"/api/jobs/{job_id}")
        assert poll.status_code == 200
        # The status names no storage path
        assert poll.json() == {"job_id": job_id, "status": "parsed"}

        artifact = await client.get(f"/api/jobs/{job_id}/artifacts/parser")
        assert artifact.status_code == 200
        assert artifact.content == artifact_storage.retrieve(parse_ref)


async def test_same_pdf_can_be_submitted_twice(
    redis_api_dependencies: tuple[
        RedisJobStateStore,
        DramatiqJobQueue[ParseJobMessage],
        FileSystemArtifactStorage,
    ],
) -> None:
    job_store, parse_queue, artifact_storage = redis_api_dependencies

    async with httpx2.AsyncClient(
        transport=ASGITransport(app),
        base_url="http://test",
    ) as client:
        responses = [
            await client.post(
                "/api/extractions",
                files={"file": ("sample.pdf", MINIMAL_PDF, "application/pdf")},
            )
            for _ in range(2)
        ]

    assert [response.status_code for response in responses] == [202, 202]
    job_ids = [UUID(response.json()["job_id"]) for response in responses]
    assert job_ids[0] != job_ids[1]

    states = [await job_store.get(job_id) for job_id in job_ids]
    assert all(state is not None for state in states)
    pdf_refs = {state.pdf_ref for state in states if state is not None}
    assert pdf_refs == {f"pdf/{hashlib.sha256(MINIMAL_PDF).hexdigest()}"}
    assert artifact_storage.retrieve(pdf_refs.pop()) == MINIMAL_PDF

    recorder = _Recorder()
    await asyncio.to_thread(parse_queue.consume, recorder, until_empty=True)
    assert {message.job_id for message in recorder.received} == set(job_ids)
