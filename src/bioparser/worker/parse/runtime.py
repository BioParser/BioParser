from threading import Event
from uuid import UUID

from bioparser.jobqueue import ExtractJobMessage, JobQueue, ParseJobMessage
from bioparser.jobqueue.dramatiq import DramatiqJobQueue, DramatiqJobSubmitter
from bioparser.jobstate import RedisJobStateStore
from bioparser.logging_config import setup_logging
from bioparser.parser.backend.factory import get_parser
from bioparser.parser.protocol import PdfParser
from bioparser.storage.filesystem import FileSystemArtifactStorage
from bioparser.worker.process import install_shutdown

from .config import WorkerSettings
from .handler import ParseJobHandler
from .health import LivenessServer


def build_worker(
    settings: WorkerSettings,
) -> tuple[JobQueue[ParseJobMessage], ParseJobHandler]:
    jobs = RedisJobStateStore(str(settings.redis_url))
    storage = FileSystemArtifactStorage(settings.artifact_storage_path)
    queue: JobQueue[ParseJobMessage] = DramatiqJobQueue.from_redis(
        str(settings.redis_url),
        name=settings.parse_queue_name,
        model=ParseJobMessage,
        timeout_s=settings.redis_timeout_seconds,
        time_limit_ms=round(settings.parse_time_limit_seconds * 1000),
        max_retries=settings.parse_max_retries,
        # One thread is supported today. The handler serializes any extra threads.
        worker_threads=1,
    )
    # The extractor process owns retries and the time limit.
    extract_queue = DramatiqJobSubmitter.from_redis(
        str(settings.redis_url),
        name=settings.extract_queue_name,
        timeout_s=settings.redis_timeout_seconds,
    )
    parser_timeout_s = settings.parser_timeout_seconds

    def parser_factory(name: str) -> PdfParser:
        return get_parser(
            name,
            timeout_s=parser_timeout_s,
            mineru_base_url=str(settings.mineru_base_url),
            mineru_tier=settings.mineru_tier,
        )

    def enqueue_extraction(job_id: UUID) -> None:
        extract_queue.submit(ExtractJobMessage(job_id=job_id))

    handler = ParseJobHandler(
        jobs=jobs,
        storage=storage,
        parser_factory=parser_factory,
        max_claims=settings.parse_max_claims,
        mineru_tier=settings.mineru_tier,
        enqueue_extraction=enqueue_extraction,
    )
    return queue, handler


def main() -> None:
    # Values come from the environment; mypy only sees the required fields.
    settings = WorkerSettings()  # type: ignore[call-arg]
    setup_logging(settings.log_level)
    liveness = LivenessServer(settings.liveness_port)
    liveness.start()
    try:
        queue, handler = build_worker(settings)
        stop = Event()
        install_shutdown(stop)
        try:
            queue.consume(handler, stop=stop)
        finally:
            handler.close()
    finally:
        liveness.close()
