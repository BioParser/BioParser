import logging
import threading
import time
from collections.abc import Generator
from threading import Event
from typing import Any

import pytest
from dramatiq import Broker, Worker
from dramatiq.brokers.stub import StubBroker
from dramatiq.middleware import CurrentMessage, Retries
from dramatiq.middleware.time_limit import TimeLimit, TimeLimitExceeded
from redis.exceptions import ConnectionError as RedisConnectionError

from bioparser.jobqueue import (
    JobHandler,
    JobQueueConfigError,
    JobQueueError,
    ParseJobMessage,
    RedisJobQueue,
    create_redis_broker,
)
from bioparser.jobqueue import redis_queue as redis_queue_module


@pytest.fixture
def stub_broker() -> Generator[StubBroker]:
    broker = StubBroker(fail_fast_default=False)
    yield broker
    broker.flush_all()
    broker.close()


def _message(job_id: str = "3f2b8c1e-9a4d-4f6b-8c2e-1d5a7b9c0e34") -> ParseJobMessage:
    return ParseJobMessage.model_validate({"job_id": job_id})


def _queue(
    broker: StubBroker, *, name: str = "parse", **options: Any
) -> RedisJobQueue[ParseJobMessage]:
    """Queue that shuts down promptly: the production worker timeout costs ~2s per consume."""
    options.setdefault("worker_timeout_ms", 50)
    return RedisJobQueue(name=name, model=ParseJobMessage, broker=broker, **options)


class _Recorder(JobHandler[ParseJobMessage]):
    """Handler that records every callback and optionally fails on each message."""

    def __init__(self, *, raises: BaseException | None = None) -> None:
        self.received: list[ParseJobMessage] = []
        self.malformed: list[object] = []
        self.failed: list[tuple[ParseJobMessage, BaseException]] = []
        self._raises = raises

    def handle(self, message: ParseJobMessage) -> None:
        self.received.append(message)
        if self._raises is not None:
            raise self._raises

    def on_malformed(self, payload: object) -> None:
        self.malformed.append(payload)

    def on_failed(self, message: ParseJobMessage, exc: BaseException) -> None:
        self.failed.append((message, exc))


def test_create_redis_broker_sets_socket_timeouts() -> None:
    broker = create_redis_broker("redis://localhost:6379/0", timeout_s=2.5)
    try:
        kwargs = broker.client.connection_pool.connection_kwargs
        assert kwargs["socket_timeout"] == 2.5
        assert kwargs["socket_connect_timeout"] == 2.5
    finally:
        broker.close()


def test_create_redis_broker_rejects_non_positive_timeout() -> None:
    with pytest.raises(JobQueueConfigError, match="timeout_s"):
        create_redis_broker("redis://localhost:6379/0", timeout_s=0)


def test_create_redis_broker_rejects_unsupported_url_scheme() -> None:
    with pytest.raises(JobQueueConfigError, match="schemes") as exc_info:
        create_redis_broker("http://localhost:6379/0")
    assert isinstance(exc_info.value.__cause__, ValueError)


def test_rejects_empty_queue_name(stub_broker: StubBroker) -> None:
    with pytest.raises(JobQueueConfigError, match="name"):
        RedisJobQueue(name="", model=ParseJobMessage, broker=stub_broker)


def test_rejects_whitespace_queue_name(stub_broker: StubBroker) -> None:
    with pytest.raises(JobQueueConfigError, match="name"):
        RedisJobQueue(name="  \t", model=ParseJobMessage, broker=stub_broker)


def test_rejects_negative_max_retries(stub_broker: StubBroker) -> None:
    with pytest.raises(JobQueueConfigError, match="max_retries"):
        RedisJobQueue(name="parse", model=ParseJobMessage, broker=stub_broker, max_retries=-1)


def test_rejects_non_positive_time_limit(stub_broker: StubBroker) -> None:
    with pytest.raises(JobQueueConfigError, match="time_limit_ms"):
        RedisJobQueue(name="parse", model=ParseJobMessage, broker=stub_broker, time_limit_ms=0)


def test_rejects_non_positive_min_backoff(stub_broker: StubBroker) -> None:
    with pytest.raises(JobQueueConfigError, match="min_backoff_ms"):
        RedisJobQueue(name="parse", model=ParseJobMessage, broker=stub_broker, min_backoff_ms=0)


def test_rejects_non_positive_worker_threads(stub_broker: StubBroker) -> None:
    with pytest.raises(JobQueueConfigError, match="worker_threads"):
        RedisJobQueue(name="parse", model=ParseJobMessage, broker=stub_broker, worker_threads=0)


def test_rejects_non_positive_worker_timeout(stub_broker: StubBroker) -> None:
    with pytest.raises(JobQueueConfigError, match="worker_timeout_ms"):
        RedisJobQueue(name="parse", model=ParseJobMessage, broker=stub_broker, worker_timeout_ms=0)


def test_rejects_queue_name_starting_with_digit(stub_broker: StubBroker) -> None:
    with pytest.raises(JobQueueConfigError, match="letter"):
        RedisJobQueue(name="123name", model=ParseJobMessage, broker=stub_broker)


def test_rejects_duplicate_queue_name_on_same_broker(stub_broker: StubBroker) -> None:
    RedisJobQueue(name="parse", model=ParseJobMessage, broker=stub_broker)
    with pytest.raises(JobQueueConfigError, match="already registered"):
        RedisJobQueue(name="parse", model=ParseJobMessage, broker=stub_broker)


def test_submit_and_consume_round_trip(stub_broker: StubBroker) -> None:
    handler = _Recorder()
    queue = _queue(stub_broker)
    expected = _message()
    queue.submit(expected)
    queue.consume(handler, until_empty=True)
    assert handler.received == [expected]


def test_worker_settings_reach_the_dramatiq_worker(
    stub_broker: StubBroker, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def capture(broker: Broker, **kwargs: Any) -> Worker:
        captured.update(kwargs)
        return Worker(broker, **kwargs)

    monkeypatch.setattr(redis_queue_module, "Worker", capture)
    handler = _Recorder()
    queue = RedisJobQueue(
        name="parse",
        model=ParseJobMessage,
        broker=stub_broker,
        worker_threads=2,
        worker_timeout_ms=50,
    )
    expected = _message()
    queue.submit(expected)
    queue.consume(handler, until_empty=True)
    assert captured["worker_threads"] == 2
    assert captured["worker_timeout"] == 50
    assert handler.received == [expected]


def test_submit_wraps_redis_errors(stub_broker: StubBroker) -> None:
    queue = _queue(stub_broker)

    def fail(_payload: object) -> None:
        raise RedisConnectionError("refused")

    queue._actor.send = fail  # type: ignore[assignment]
    with pytest.raises(JobQueueError, match="enqueue") as exc_info:
        queue.submit(_message())
    assert isinstance(exc_info.value.__cause__, RedisConnectionError)


def test_consume_join_wraps_redis_errors(stub_broker: StubBroker) -> None:
    queue = _queue(stub_broker)

    def fail_join(_name: str) -> None:
        raise RedisConnectionError("refused")

    stub_broker.join = fail_join  # type: ignore[assignment]
    with pytest.raises(JobQueueError, match="drain") as exc_info:
        queue.consume(_Recorder(), until_empty=True)
    assert isinstance(exc_info.value.__cause__, RedisConnectionError)
    assert queue._consuming.acquire(blocking=False)


def test_rejects_consume_while_already_consuming(stub_broker: StubBroker) -> None:
    queue = _queue(stub_broker)
    assert queue._consuming.acquire(blocking=False)
    try:
        with pytest.raises(JobQueueError, match="already being consumed"):
            queue.consume(_Recorder(), until_empty=True)
    finally:
        queue._consuming.release()


def test_consume_can_run_again_after_returning(stub_broker: StubBroker) -> None:
    handler = _Recorder()
    queue = _queue(stub_broker)
    queue.submit(_message("3f2b8c1e-9a4d-4f6b-8c2e-1d5a7b9c0e31"))
    queue.consume(handler, until_empty=True)
    queue.submit(_message("3f2b8c1e-9a4d-4f6b-8c2e-1d5a7b9c0e32"))
    queue.consume(handler, until_empty=True)
    assert handler.received == [
        _message("3f2b8c1e-9a4d-4f6b-8c2e-1d5a7b9c0e31"),
        _message("3f2b8c1e-9a4d-4f6b-8c2e-1d5a7b9c0e32"),
    ]


def test_consume_returns_when_stop_is_already_set(stub_broker: StubBroker) -> None:
    stop = Event()
    stop.set()
    queue = _queue(stub_broker)
    queue.consume(_Recorder(), stop=stop)


def test_consume_returns_when_handler_sets_stop(stub_broker: StubBroker) -> None:
    stop = Event()

    class Stopper(_Recorder):
        def handle(self, message: ParseJobMessage) -> None:
            super().handle(message)
            stop.set()

    handler = Stopper()
    queue = _queue(stub_broker)
    expected = _message()
    queue.submit(expected)
    queue.consume(handler, stop=stop)
    assert handler.received == [expected]


def test_named_queues_are_independent(stub_broker: StubBroker) -> None:
    parse_handler = _Recorder()
    extract_handler = _Recorder()
    parse = _queue(stub_broker)
    extract = _queue(stub_broker, name="extract")
    parse.submit(_message("3f2b8c1e-9a4d-4f6b-8c2e-1d5a7b9c0e33"))
    extract.consume(extract_handler, until_empty=True)
    parse.consume(parse_handler, until_empty=True)
    assert extract_handler.received == []
    assert parse_handler.received == [_message("3f2b8c1e-9a4d-4f6b-8c2e-1d5a7b9c0e33")]


def test_malformed_payload_is_poisoned(stub_broker: StubBroker) -> None:
    handler = _Recorder()
    queue = _queue(stub_broker)
    queue._actor.send({"job_id": "only-id"})
    queue.submit(_message("3f2b8c1e-9a4d-4f6b-8c2e-1d5a7b9c0e35"))
    queue.consume(handler, until_empty=True)
    assert handler.received == [_message("3f2b8c1e-9a4d-4f6b-8c2e-1d5a7b9c0e35")]
    assert handler.malformed == [{"job_id": "only-id"}]


def test_non_object_payload_notifies_malformed(stub_broker: StubBroker) -> None:
    handler = _Recorder()
    queue = _queue(stub_broker)
    queue._actor.send(["not", "an", "object"])
    queue.consume(handler, until_empty=True)
    assert handler.malformed == [["not", "an", "object"]]


def test_malformed_does_not_call_on_failed(stub_broker: StubBroker) -> None:
    handler = _Recorder()
    queue = _queue(stub_broker, max_retries=0)
    queue._actor.send({"job_id": "only-id"})
    queue.consume(handler, until_empty=True)
    assert handler.failed == []


def test_wrong_schema_version_is_poisoned(stub_broker: StubBroker) -> None:
    handler = _Recorder()
    queue = _queue(stub_broker)
    bad = {
        "schema_version": 99,
        "job_id": "job-1",
    }
    queue._actor.send(bad)
    queue.consume(handler, until_empty=True)
    assert handler.received == []
    assert handler.malformed == [bad]


def test_malformed_log_names_fields_without_values(
    stub_broker: StubBroker, caplog: pytest.LogCaptureFixture
) -> None:
    queue = _queue(stub_broker)
    queue._actor.send(
        {
            "job_id": "job-1",
            "token": "s3cret",
        }
    )
    with caplog.at_level(logging.WARNING, logger="bioparser.jobqueue.redis_queue"):
        queue.consume(_Recorder(), until_empty=True)
    assert "s3cret" not in caplog.text
    assert "token: extra_forbidden" in caplog.text


def test_malformed_hook_error_still_acks(stub_broker: StubBroker) -> None:
    class Boom(_Recorder):
        def on_malformed(self, payload: object) -> None:
            raise RuntimeError("hook failed")

    handler = Boom()
    queue = _queue(stub_broker)
    queue._actor.send({"job_id": "only-id"})
    queue.submit(_message("3f2b8c1e-9a4d-4f6b-8c2e-1d5a7b9c0e35"))
    queue.consume(handler, until_empty=True)
    assert handler.received == [_message("3f2b8c1e-9a4d-4f6b-8c2e-1d5a7b9c0e35")]


def test_dispatch_without_handler_raises(stub_broker: StubBroker) -> None:
    queue = _queue(stub_broker)
    with pytest.raises(JobQueueError, match="no handler"):
        queue._dispatch(_message().model_dump(mode="json"))


def test_handler_failure_calls_on_failed_after_retries(stub_broker: StubBroker) -> None:
    handler = _Recorder(raises=RuntimeError("parse failed"))
    queue = _queue(stub_broker, max_retries=2, min_backoff_ms=1)
    expected = _message()
    queue.submit(expected)
    queue.consume(handler, until_empty=True)
    assert handler.received == [expected] * 3
    assert len(handler.failed) == 1
    assert handler.failed[0][0] == expected
    assert isinstance(handler.failed[0][1], RuntimeError)


def test_time_limit_calls_on_failed_after_retries(stub_broker: StubBroker) -> None:
    handler = _Recorder(raises=TimeLimitExceeded())
    queue = _queue(stub_broker, max_retries=0)
    expected = _message()
    queue.submit(expected)
    queue.consume(handler, until_empty=True)
    assert len(handler.failed) == 1
    assert handler.failed[0][0] == expected
    assert isinstance(handler.failed[0][1], TimeLimitExceeded)


class _Hanging(JobHandler[ParseJobMessage]):
    """Handler that never finishes on its own."""

    def __init__(self) -> None:
        self.interrupted = Event()
        self.failed: list[BaseException] = []

    def handle(self, message: ParseJobMessage) -> None:
        try:
            for _ in range(1000):
                time.sleep(0.01)
        except TimeLimitExceeded:
            self.interrupted.set()
            raise

    def on_failed(self, message: ParseJobMessage, exc: BaseException) -> None:
        self.failed.append(exc)


def test_consume_enforces_the_time_limit(stub_broker: StubBroker) -> None:
    """The broker is not booted by the test: consume must do what Dramatiq's CLI would."""
    (time_limit,) = [m for m in stub_broker.middleware if isinstance(m, TimeLimit)]
    time_limit.manager.interval = 0.05  # type: ignore[union-attr]
    queue = _queue(stub_broker, time_limit_ms=200, max_retries=0)
    handler = _Hanging()
    queue.submit(_message())

    queue.consume(handler, until_empty=True)

    assert handler.interrupted.is_set()
    assert [type(exc) for exc in handler.failed] == [TimeLimitExceeded]


def test_consume_can_run_more_than_once_on_one_broker(stub_broker: StubBroker) -> None:
    """The boot event starts a thread, and a thread starts only once."""
    queue = _queue(stub_broker)
    queue.consume(_Recorder(), until_empty=True)

    queue.consume(_Recorder(), until_empty=True)


def test_redis_broker_has_the_middleware_the_queue_relies_on() -> None:
    broker = create_redis_broker("redis://localhost:6379/0")

    kinds = {type(middleware) for middleware in broker.middleware}
    RedisJobQueue(name="parse", model=ParseJobMessage, broker=broker)

    assert {TimeLimit, Retries} <= kinds
    assert CurrentMessage in {type(middleware) for middleware in broker.middleware}


def test_queue_options_reach_the_actor(stub_broker: StubBroker) -> None:
    _queue(stub_broker, time_limit_ms=1234, max_retries=7, min_backoff_ms=99)

    options = stub_broker.get_actor("parse").options
    assert options["time_limit"] == 1234
    assert options["max_retries"] == 7
    assert options["min_backoff"] == 99


def test_stop_waits_for_the_running_job_to_finish(stub_broker: StubBroker) -> None:
    """Shutdown finishes the current job instead of interrupting it."""
    started, finished = Event(), Event()

    class _Slow(JobHandler[ParseJobMessage]):
        def handle(self, message: ParseJobMessage) -> None:
            started.set()
            time.sleep(0.3)
            finished.set()

    stop = Event()
    queue = _queue(stub_broker)
    queue.submit(_message())

    def stop_once_started() -> None:
        started.wait(5)
        stop.set()

    threading.Thread(target=stop_once_started, daemon=True).start()

    queue.consume(_Slow(), stop=stop)

    assert finished.is_set()
