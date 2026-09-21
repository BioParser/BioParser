from collections.abc import Generator
from threading import Event

import pytest
from dramatiq.brokers.stub import StubBroker
from dramatiq.middleware.time_limit import TimeLimitExceeded
from redis.exceptions import ConnectionError as RedisConnectionError

from bioparser.jobqueue import (
    JobQueueConfigError,
    JobQueueError,
    ParseJobMessage,
    RedisJobQueue,
    create_redis_broker,
)


@pytest.fixture
def stub_broker() -> Generator[StubBroker]:
    broker = StubBroker(fail_fast_default=False)
    broker.emit_after("process_boot")
    yield broker
    broker.flush_all()
    broker.close()


def _message(job_id: str = "job-1") -> ParseJobMessage:
    return ParseJobMessage(job_id=job_id, document_id="doc-1", input_pdf_ref="pdf-1")


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


def test_rejects_queue_name_starting_with_digit(stub_broker: StubBroker) -> None:
    with pytest.raises(JobQueueConfigError, match="letter"):
        RedisJobQueue(name="123name", model=ParseJobMessage, broker=stub_broker)


def test_rejects_duplicate_queue_name_on_same_broker(stub_broker: StubBroker) -> None:
    RedisJobQueue(name="parse", model=ParseJobMessage, broker=stub_broker)
    with pytest.raises(JobQueueConfigError, match="already registered"):
        RedisJobQueue(name="parse", model=ParseJobMessage, broker=stub_broker)


def test_submit_and_consume_round_trip(stub_broker: StubBroker) -> None:
    received: list[ParseJobMessage] = []
    queue = RedisJobQueue(name="parse", model=ParseJobMessage, broker=stub_broker)
    expected = _message()
    queue.submit(expected)
    queue.consume(received.append, until_empty=True)
    assert received == [expected]


def test_submit_wraps_redis_errors(stub_broker: StubBroker) -> None:
    queue = RedisJobQueue(name="parse", model=ParseJobMessage, broker=stub_broker)

    def fail(_payload: object) -> None:
        raise RedisConnectionError("refused")

    queue._actor.send = fail  # type: ignore[assignment]
    with pytest.raises(JobQueueError, match="enqueue") as exc_info:
        queue.submit(_message())
    assert isinstance(exc_info.value.__cause__, RedisConnectionError)


def test_consume_join_wraps_redis_errors(stub_broker: StubBroker) -> None:
    queue = RedisJobQueue(name="parse", model=ParseJobMessage, broker=stub_broker)

    def fail_join(_name: str) -> None:
        raise RedisConnectionError("refused")

    stub_broker.join = fail_join  # type: ignore[assignment]
    with pytest.raises(JobQueueError, match="drain") as exc_info:
        queue.consume(lambda message: None, until_empty=True)
    assert isinstance(exc_info.value.__cause__, RedisConnectionError)


def test_consume_returns_when_stop_is_already_set(stub_broker: StubBroker) -> None:
    stop = Event()
    stop.set()
    queue = RedisJobQueue(name="parse", model=ParseJobMessage, broker=stub_broker)
    queue.consume(lambda message: None, stop=stop)


def test_consume_returns_when_handler_sets_stop(stub_broker: StubBroker) -> None:
    received: list[ParseJobMessage] = []
    stop = Event()
    queue = RedisJobQueue(name="parse", model=ParseJobMessage, broker=stub_broker)
    expected = _message()
    queue.submit(expected)

    def handler(message: ParseJobMessage) -> None:
        received.append(message)
        stop.set()

    queue.consume(handler, stop=stop)
    assert received == [expected]


def test_named_queues_are_independent(stub_broker: StubBroker) -> None:
    parse_received: list[ParseJobMessage] = []
    extract_received: list[ParseJobMessage] = []
    parse = RedisJobQueue(name="parse", model=ParseJobMessage, broker=stub_broker)
    extract = RedisJobQueue(name="extract", model=ParseJobMessage, broker=stub_broker)
    parse.submit(_message("parse-job"))
    extract.consume(extract_received.append, until_empty=True)
    parse.consume(parse_received.append, until_empty=True)
    assert extract_received == []
    assert parse_received == [_message("parse-job")]


def test_malformed_payload_is_poisoned(stub_broker: StubBroker) -> None:
    received: list[ParseJobMessage] = []
    malformed: list[dict[str, object]] = []
    queue = RedisJobQueue(
        name="parse",
        model=ParseJobMessage,
        broker=stub_broker,
        on_malformed=malformed.append,
    )
    queue._actor.send({"job_id": "only-id"})
    queue.submit(_message("good"))
    queue.consume(received.append, until_empty=True)
    assert received == [_message("good")]
    assert malformed == [{"job_id": "only-id"}]


def test_wrong_schema_version_is_poisoned(stub_broker: StubBroker) -> None:
    received: list[ParseJobMessage] = []
    malformed: list[dict[str, object]] = []
    queue = RedisJobQueue(
        name="parse",
        model=ParseJobMessage,
        broker=stub_broker,
        on_malformed=malformed.append,
    )
    bad = {
        "schema_version": 99,
        "job_id": "job-1",
        "document_id": "doc-1",
        "input_pdf_ref": "pdf-1",
    }
    queue._actor.send(bad)
    queue.consume(received.append, until_empty=True)
    assert received == []
    assert malformed == [bad]


def test_malformed_hook_error_still_acks(stub_broker: StubBroker) -> None:
    received: list[ParseJobMessage] = []

    def boom(payload: dict[str, object]) -> None:
        raise RuntimeError("hook failed")

    queue = RedisJobQueue(
        name="parse",
        model=ParseJobMessage,
        broker=stub_broker,
        on_malformed=boom,
    )
    queue._actor.send({"job_id": "only-id"})
    queue.submit(_message("good"))
    queue.consume(received.append, until_empty=True)
    assert received == [_message("good")]


def test_handler_failure_calls_on_failed_after_retries(stub_broker: StubBroker) -> None:
    failed: list[tuple[ParseJobMessage, BaseException]] = []

    def fail(message: ParseJobMessage) -> None:
        raise RuntimeError("parse failed")

    queue = RedisJobQueue(
        name="parse",
        model=ParseJobMessage,
        broker=stub_broker,
        max_retries=0,
        on_failed=lambda message, exc: failed.append((message, exc)),
    )
    expected = _message()
    queue.submit(expected)
    queue.consume(fail, until_empty=True)
    assert len(failed) == 1
    assert failed[0][0] == expected
    assert isinstance(failed[0][1], RuntimeError)


def test_time_limit_calls_on_failed_after_retries(stub_broker: StubBroker) -> None:
    failed: list[tuple[ParseJobMessage, BaseException]] = []

    def timeout(message: ParseJobMessage) -> None:
        raise TimeLimitExceeded()

    queue = RedisJobQueue(
        name="parse",
        model=ParseJobMessage,
        broker=stub_broker,
        max_retries=0,
        on_failed=lambda message, exc: failed.append((message, exc)),
    )
    expected = _message()
    queue.submit(expected)
    queue.consume(timeout, until_empty=True)
    assert len(failed) == 1
    assert failed[0][0] == expected
    assert isinstance(failed[0][1], TimeLimitExceeded)
