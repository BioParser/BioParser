import asyncio
import hashlib
import os
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from uuid import UUID

import httpx2
import pytest
from conftest import TEST_API_KEY
from httpx2 import ASGITransport
from redis.asyncio import Redis
from sqlalchemy.engine import make_url

from bioparser.api.app import app
from bioparser.db import engine as database
from bioparser.db import jobs as job_queries
from bioparser.db.migrate import sync_database_url, upgrade
from bioparser.extractor_names import DEFAULT_EXTRACTOR_NAME
from bioparser.jobqueue import ParseJobMessage
from bioparser.jobqueue.dramatiq import DramatiqJobConsumer, DramatiqJobSubmitter
from bioparser.jobqueue.dramatiq.redis_queue import _create_redis_broker
from bioparser.jobstate import JobState
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

    async def handle(self, message: ParseJobMessage) -> None:
        self.received.append(message)

    async def on_malformed(self, payload: object) -> None:
        pytest.fail(f"queue produced a malformed payload: {payload!r}")

    async def on_failed(self, message: ParseJobMessage, exc: BaseException) -> None:
        pytest.fail(f"queue delivery failed for {message.job_id}: {exc}")

    async def aopen(self) -> None:
        return

    async def aclose(self) -> None:
        return


def _create_database(admin_url: str, name: str) -> None:
    from sqlalchemy import create_engine, text

    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    engine.dispose()


def _drop_database(admin_url: str, name: str) -> None:
    from sqlalchemy import create_engine, text

    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as connection:
        connection.execute(
            text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :name"),
            {"name": name},
        )
        connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
    engine.dispose()


@pytest.fixture
async def redis_api_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> AsyncGenerator[tuple[DramatiqJobConsumer[ParseJobMessage], FileSystemArtifactStorage]]:
    # The queue stays on Redis. Job rows go to a throwaway Postgres database.
    raw = os.environ.get("BIOPARSER_TEST_DATABASE_URL")
    if not raw:
        pytest.skip("BIOPARSER_TEST_DATABASE_URL is not set")
    probe = Redis.from_url(TEST_REDIS_URL)
    try:
        await probe.ping()
    except Exception as exc:  # noqa: BLE001 - any connection failure means "skip"
        await probe.aclose()
        pytest.skip(f"no Redis available at {TEST_REDIS_URL}: {exc}")
    await probe.aclose()

    unique_id = uuid.uuid4().hex
    broker_namespace = f"bioparser-test-api-{unique_id}"
    queue_name = f"parse_{unique_id}"
    admin_url = (
        make_url(sync_database_url(raw))
        .set(database="postgres")
        .render_as_string(hide_password=False)
    )
    name = f"api_{unique_id}"
    _create_database(admin_url, name)
    bare = (
        make_url(raw)
        .set(database=name)
        .set(drivername="postgresql")
        .render_as_string(hide_password=False)
    )
    upgrade(bare)
    await database.open(bare)

    broker = _create_redis_broker(TEST_REDIS_URL, namespace=broker_namespace)
    # The route only sends. This consumer drains the same broker afterwards.
    parse_queue = DramatiqJobConsumer(
        name=queue_name,
        model=ParseJobMessage,
        broker=broker,
        worker_timeout_ms=50,
    )
    submitter = DramatiqJobSubmitter(name=queue_name, broker=broker)
    artifact_storage = FileSystemArtifactStorage(tmp_path / "artifacts")

    # ASGITransport does not run the production lifespan here, so inject the
    # queue and storage the routes still read from app.state. Job queries are
    # the process-wide pool opened above.
    monkeypatch.setattr(app.state, "parse_queue", submitter, raising=False)
    monkeypatch.setattr(app.state, "artifact_storage", artifact_storage, raising=False)

    try:
        yield parse_queue, artifact_storage
    finally:
        broker.flush_all()
        broker.client.delete(broker_namespace)
        broker.client.close()
        await database.aclose()
        _drop_database(admin_url, name)


async def test_redis_backed_submission_and_polling(
    redis_api_dependencies: tuple[DramatiqJobConsumer[ParseJobMessage], FileSystemArtifactStorage],
) -> None:
    """Submit and poll through Postgres job rows and a real Redis queue."""
    parse_queue, artifact_storage = redis_api_dependencies

    async with httpx2.AsyncClient(
        transport=ASGITransport(app),
        base_url="http://test",
        headers={"X-API-Key": TEST_API_KEY},
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
        async with database.transaction() as connection:
            queued = await job_queries.get(connection, parsed_job_id)
        assert queued is not None
        assert queued == JobState(
            job_id=parsed_job_id,
            status="queued",
            parser=DEFAULT_PARSER_NAME,
            extractor=DEFAULT_EXTRACTOR_NAME,
            pdf_ref=expected_pdf_ref,
        )
        assert await artifact_storage.retrieve(queued.pdf_ref) == MINIMAL_PDF

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
        async with database.transaction() as connection:
            assert (
                await job_queries.claim(
                    connection,
                    queued.job_id,
                    status="parsing",
                    from_statuses=("queued", "parsing"),
                    max_claims=2,
                )
                == "claimed"
            )
        poll = await client.get(f"/api/jobs/{job_id}")
        assert poll.status_code == 200
        assert poll.json() == {"job_id": job_id, "status": "parsing"}

        output_artifact_ref = f"art-result-{job_id}"

        await artifact_storage.store(
            ParserArtifact(
                schema_version="1",
                checksum=hashlib.sha256(MINIMAL_PDF).hexdigest(),
                parser=ParserInfo(name="test", version="1", configuration={}),
                pages=[],
            )
            .model_dump_json()
            .encode(),
            ArtifactMetadata(
                artifact_path=output_artifact_ref,
                media_type="application/json",
            ),
        )
        async with database.transaction() as connection:
            assert await job_queries.record_parsed(connection, queued.job_id, output_artifact_ref)
        poll = await client.get(f"/api/jobs/{job_id}")
        assert poll.status_code == 200
        # The status names no storage path
        assert poll.json() == {"job_id": job_id, "status": "parsed"}

        artifact = await client.get(f"/api/jobs/{job_id}/artifacts/parser")
        assert artifact.status_code == 200
        assert artifact.content == await artifact_storage.retrieve(output_artifact_ref)


async def test_same_pdf_can_be_submitted_twice(
    redis_api_dependencies: tuple[DramatiqJobConsumer[ParseJobMessage], FileSystemArtifactStorage],
) -> None:
    parse_queue, artifact_storage = redis_api_dependencies

    async with httpx2.AsyncClient(
        transport=ASGITransport(app),
        base_url="http://test",
        headers={"X-API-Key": TEST_API_KEY},
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

    states = []
    async with database.transaction() as connection:
        for job_id in job_ids:
            states.append(await job_queries.get(connection, job_id))
    assert all(state is not None for state in states)
    pdf_refs = {state.pdf_ref for state in states if state is not None}
    assert pdf_refs == {f"pdf/{hashlib.sha256(MINIMAL_PDF).hexdigest()}"}
    assert await artifact_storage.retrieve(pdf_refs.pop()) == MINIMAL_PDF

    recorder = _Recorder()
    await asyncio.to_thread(parse_queue.consume, recorder, until_empty=True)
    assert {message.job_id for message in recorder.received} == set(job_ids)
