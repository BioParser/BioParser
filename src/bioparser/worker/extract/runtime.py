from threading import Event

from bioparser.extract.backend.factory import get_extractor
from bioparser.extract.protocol import Extractor
from bioparser.jobqueue import ExtractJobMessage, JobQueue
from bioparser.jobqueue.dramatiq import DramatiqJobQueue
from bioparser.logging_config import setup_logging
from bioparser.services.vllm import VLLMService
from bioparser.storage.filesystem import FileSystemArtifactStorage
from bioparser.worker.health import LivenessServer
from bioparser.worker.process import install_shutdown

from .config import ExtractWorkerSettings
from .handler import ExtractJobHandler


def build_worker(
    settings: ExtractWorkerSettings,
) -> tuple[JobQueue[ExtractJobMessage], ExtractJobHandler]:
    storage = FileSystemArtifactStorage(settings.artifact_storage_path)
    queue: JobQueue[ExtractJobMessage] = DramatiqJobQueue.from_redis(
        str(settings.redis_url),
        name=settings.extract_queue_name,
        model=ExtractJobMessage,
        timeout_s=settings.redis_timeout_seconds,
        time_limit_ms=round(settings.extract_time_limit_seconds * 1000),
        max_retries=settings.extract_max_retries,
        worker_threads=1,
    )
    vllm = VLLMService()
    char_budget = settings.extraction_char_budget
    max_tokens = settings.extraction_max_tokens

    def extractor_factory(name: str) -> Extractor:
        return get_extractor(
            name,
            vllm=vllm,
            char_budget=char_budget,
            max_tokens=max_tokens,
        )

    handler = ExtractJobHandler(
        database_url=str(settings.database_url),
        storage=storage,
        extractor_factory=extractor_factory,
        max_claims=settings.extract_max_claims,
        vllm=vllm,
    )
    return queue, handler


def main() -> None:
    settings = ExtractWorkerSettings()  # type: ignore[call-arg]
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
