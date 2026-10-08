import logging
import signal
from threading import Event

from bioparser.jobqueue import DramatiqJobQueue, JobQueue, ParseJobMessage
from bioparser.jobstate import RedisJobStateStore
from bioparser.logging_config import setup_logging
from bioparser.parser.backend.factory import get_parser
from bioparser.parser.backend.mineru.runtime import require_mineru_cli
from bioparser.parser.protocol import PdfParser
from bioparser.storage.filesystem import FileSystemArtifactStorage

from .config import WorkerSettings
from .handler import ParseJobHandler

LOGGER = logging.getLogger(__name__)


def install_shutdown(stop: Event) -> None:
    """Stop the consumer on SIGINT and SIGTERM. The queue joins in-flight work."""

    def _request_stop(signum: int, _frame: object) -> None:
        LOGGER.info(
            f"received {signal.Signals(signum).name}. Finishing the current job, then shutting down"
        )
        stop.set()

    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGTERM, _request_stop)


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
    parser_timeout_s = settings.parser_timeout_seconds

    def parser_factory(name: str) -> PdfParser:
        return get_parser(name, timeout_s=parser_timeout_s)

    handler = ParseJobHandler(
        jobs=jobs,
        storage=storage,
        parser_factory=parser_factory,
        max_claims=settings.parse_max_claims,
    )
    return queue, handler


def main() -> None:
    # Values come from the environment; mypy only sees the required fields.
    settings = WorkerSettings()  # type: ignore[call-arg]
    setup_logging(settings.log_level)
    require_mineru_cli()
    queue, handler = build_worker(settings)
    stop = Event()
    install_shutdown(stop)
    try:
        queue.consume(handler, stop=stop)
    finally:
        handler.close()
