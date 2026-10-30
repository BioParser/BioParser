import asyncio
import logging
import threading
import time
from collections.abc import Generator
from queue import Empty
from threading import Event
from typing import Any, cast

import pytest
from dramatiq import Broker, Message, Worker
from dramatiq.brokers.stub import StubBroker
from dramatiq.common import current_millis, dq_name
from dramatiq.errors import ActorNotFound
from dramatiq.middleware import CurrentMessage, Retries
from dramatiq.middleware.time_limit import TimeLimit
from redis.exceptions import ConnectionError as RedisConnectionError

from bioparser.jobqueue import (
    JobDeferralExpired,
    JobDeferred,
    JobHandler,
    JobQueueConfigError,
    JobQueueError,
    JobTimeLimitExceeded,
    ParseJobMessage,
)
from bioparser.jobqueue.dramatiq import DramatiqJobConsumer, DramatiqJobSubmitter
from bioparser.jobqueue.dramatiq import redis_queue as redis_queue_module
from bioparser.jobqueue.dramatiq.redis_queue import _create_redis_broker
from bioparser.jobqueue.dramatiq.watchdog import DeliveryGuard


@pytest.fixture
def stub_broker() -> Generator[StubBroker]:
    broker = StubBroker(fail_fast_default=False)
    yield broker
    broker.flush_all()
    broker.close()


def _message(job_id: str = "3f2b8c1e-9a4d-4f6b-8c2e-1d5a7b9c0e34") -> ParseJobMessage:
    return ParseJobMessage.model_validate({"job_id": job_id})


def _submit(
    queue: DramatiqJobConsumer[ParseJobMessage] | DramatiqJobSubmitter, message: ParseJobMessage
) -> None:
    """Enqueue through a submitter. A consumer queue shares its broker with one."""
    if isinstance(queue, DramatiqJobConsumer):
        sender = DramatiqJobSubmitter(name=queue._name, broker=queue._broker)
    else:
        sender = queue
    asyncio.run(sender.submit(message))


def _queue(
    broker: StubBroker, *, name: str = "parse", **options: Any
) -> DramatiqJobConsumer[ParseJobMessage]:
    """Queue that shuts down promptly: the production worker timeout costs ~2s per consume."""
    options.setdefault("worker_timeout_ms", 50)
    return DramatiqJobConsumer(name=name, model=ParseJobMessage, broker=broker, **options)


class _Recorder(JobHandler[ParseJobMessage]):
    """Handler that records every callback and optionally fails on each message."""

    def __init__(self, *, raises: BaseException | None = None) -> None:
        self.received: list[ParseJobMessage] = []
        self.malformed: list[object] = []
        self.failed: list[tuple[ParseJobMessage, BaseException]] = []
        self._raises = raises

    async def handle(self, message: ParseJobMessage) -> None:
        self.received.append(message)
        if self._raises is not None:
            raise self._raises

    async def on_malformed(self, payload: object) -> None:
        self.malformed.append(payload)

    async def on_failed(self, message: ParseJobMessage, exc: BaseException) -> None:
        self.failed.append((message, exc))


def test_redis_broker_sets_socket_timeouts() -> None:
    broker = _create_redis_broker("redis://localhost:6379/0", timeout_s=2.5)
    try:
        kwargs = broker.client.connection_pool.connection_kwargs
        assert kwargs["socket_timeout"] == 2.5
        assert kwargs["socket_connect_timeout"] == 2.5
    finally:
        broker.close()


def test_redis_broker_rejects_non_positive_timeout() -> None:
    with pytest.raises(JobQueueConfigError, match="timeout_s"):
        _create_redis_broker("redis://localhost:6379/0", timeout_s=0)


def test_redis_broker_rejects_unsupported_url_scheme() -> None:
    with pytest.raises(JobQueueConfigError, match="invalid Redis URL") as exc_info:
        _create_redis_broker("http://localhost:6379/0")
    assert isinstance(exc_info.value.__cause__, ValueError)


def test_create_redis_broker_error_holds_neither_the_url_nor_the_value_error_text() -> None:
    # An unescaped "/" in the password ends the host part early, so redis-py's
    # port error quotes the start of the password.
    with pytest.raises(JobQueueConfigError) as exc_info:
        _create_redis_broker("redis://:planted/secret@cache.internal:6379/0")

    cause = exc_info.value.__cause__
    assert isinstance(cause, ValueError)
    assert "planted" in str(cause)  # the text that used to be copied
    message = str(exc_info.value)
    assert message == "invalid Redis URL"
    assert str(cause) not in message


def test_rejects_empty_queue_name(stub_broker: StubBroker) -> None:
    with pytest.raises(JobQueueConfigError, match="name"):
        DramatiqJobConsumer(name="", model=ParseJobMessage, broker=stub_broker)


def test_rejects_whitespace_queue_name(stub_broker: StubBroker) -> None:
    with pytest.raises(JobQueueConfigError, match="name"):
        DramatiqJobConsumer(name="  \t", model=ParseJobMessage, broker=stub_broker)


def test_rejects_negative_max_retries(stub_broker: StubBroker) -> None:
    with pytest.raises(JobQueueConfigError, match="max_retries"):
        DramatiqJobConsumer(name="parse", model=ParseJobMessage, broker=stub_broker, max_retries=-1)


def test_rejects_non_positive_time_limit(stub_broker: StubBroker) -> None:
    with pytest.raises(JobQueueConfigError, match="time_limit_ms"):
        DramatiqJobConsumer(
            name="parse", model=ParseJobMessage, broker=stub_broker, time_limit_ms=0
        )


def test_rejects_non_positive_min_backoff(stub_broker: StubBroker) -> None:
    with pytest.raises(JobQueueConfigError, match="min_backoff_ms"):
        DramatiqJobConsumer(
            name="parse", model=ParseJobMessage, broker=stub_broker, min_backoff_ms=0
        )


def test_rejects_non_positive_worker_timeout(stub_broker: StubBroker) -> None:
    with pytest.raises(JobQueueConfigError, match="worker_timeout_ms"):
        DramatiqJobConsumer(
            name="parse", model=ParseJobMessage, broker=stub_broker, worker_timeout_ms=0
        )


def test_rejects_queue_name_starting_with_digit(stub_broker: StubBroker) -> None:
    with pytest.raises(JobQueueConfigError, match="letter"):
        DramatiqJobConsumer(name="123name", model=ParseJobMessage, broker=stub_broker)

    assert "123name" not in stub_broker.actors


def test_rejects_duplicate_queue_name_on_same_broker(stub_broker: StubBroker) -> None:
    DramatiqJobConsumer(name="parse", model=ParseJobMessage, broker=stub_broker)
    with pytest.raises(JobQueueConfigError, match="already registered"):
        DramatiqJobConsumer(name="parse", model=ParseJobMessage, broker=stub_broker)


def test_consume_opens_the_handler_before_messages_and_closes_it_after(
    stub_broker: StubBroker,
) -> None:
    events: list[str] = []

    class _Lifecycle(JobHandler[ParseJobMessage]):
        async def aopen(self) -> None:
            events.append("open")

        async def handle(self, message: ParseJobMessage) -> None:
            events.append("handle")

        async def aclose(self) -> None:
            events.append("close")

    queue = _queue(stub_broker)
    _submit(queue, _message())
    queue.consume(_Lifecycle(), until_empty=True)

    assert events == ["open", "handle", "close"]


def test_submit_and_consume_round_trip(stub_broker: StubBroker) -> None:
    handler = _Recorder()
    queue = _queue(stub_broker)
    expected = _message()
    _submit(queue, expected)
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
    queue = DramatiqJobConsumer(
        name="parse",
        model=ParseJobMessage,
        broker=stub_broker,
        worker_timeout_ms=50,
    )
    expected = _message()
    _submit(queue, expected)
    queue.consume(handler, until_empty=True)
    assert captured["worker_threads"] == 1
    assert captured["worker_timeout"] == 50
    assert handler.received == [expected]


def test_submit_wraps_redis_errors(stub_broker: StubBroker) -> None:
    def fail(_message: object) -> None:
        raise RedisConnectionError("refused")

    stub_broker.enqueue = fail  # type: ignore[method-assign, assignment]
    submitter = DramatiqJobSubmitter(name="parse", broker=stub_broker)
    with pytest.raises(JobQueueError, match="enqueue") as exc_info:
        asyncio.run(submitter.submit(_message()))
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
    _submit(queue, _message("3f2b8c1e-9a4d-4f6b-8c2e-1d5a7b9c0e31"))
    queue.consume(handler, until_empty=True)
    _submit(queue, _message("3f2b8c1e-9a4d-4f6b-8c2e-1d5a7b9c0e32"))
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
        async def handle(self, message: ParseJobMessage) -> None:
            await super().handle(message)
            stop.set()

    handler = Stopper()
    queue = _queue(stub_broker)
    expected = _message()
    _submit(queue, expected)
    queue.consume(handler, stop=stop)
    assert handler.received == [expected]


def test_named_queues_are_independent(stub_broker: StubBroker) -> None:
    parse_handler = _Recorder()
    extract_handler = _Recorder()
    parse = _queue(stub_broker)
    extract = _queue(stub_broker, name="extract")
    _submit(parse, _message("3f2b8c1e-9a4d-4f6b-8c2e-1d5a7b9c0e33"))
    extract.consume(extract_handler, until_empty=True)
    parse.consume(parse_handler, until_empty=True)
    assert extract_handler.received == []
    assert parse_handler.received == [_message("3f2b8c1e-9a4d-4f6b-8c2e-1d5a7b9c0e33")]


def test_malformed_payload_is_poisoned(stub_broker: StubBroker) -> None:
    handler = _Recorder()
    queue = _queue(stub_broker)
    queue._actor.send({"job_id": "only-id"})
    _submit(queue, _message("3f2b8c1e-9a4d-4f6b-8c2e-1d5a7b9c0e35"))
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
    with caplog.at_level(logging.WARNING, logger="bioparser.jobqueue.dramatiq.redis_queue"):
        queue.consume(_Recorder(), until_empty=True)
    assert "s3cret" not in caplog.text
    assert "token: extra_forbidden" in caplog.text


def test_malformed_hook_error_still_acks(stub_broker: StubBroker) -> None:
    class Boom(_Recorder):
        async def on_malformed(self, payload: object) -> None:
            raise RuntimeError("hook failed")

    handler = Boom()
    queue = _queue(stub_broker)
    queue._actor.send({"job_id": "only-id"})
    _submit(queue, _message("3f2b8c1e-9a4d-4f6b-8c2e-1d5a7b9c0e35"))
    queue.consume(handler, until_empty=True)
    assert handler.received == [_message("3f2b8c1e-9a4d-4f6b-8c2e-1d5a7b9c0e35")]


def test_dispatch_without_handler_raises(stub_broker: StubBroker) -> None:
    queue = _queue(stub_broker)
    with pytest.raises(JobQueueError, match="no handler"):
        queue._dispatch(_message().model_dump(mode="json"))


def test_deferred_message_is_sent_again_without_a_retry(stub_broker: StubBroker) -> None:
    """Call the actor directly. consume(until_empty=True) waits on the delay queue."""
    handler = _Recorder(raises=JobDeferred("vLLM is not answering"))
    queue = _queue(stub_broker, max_retries=0)
    expected = _message()
    payload = expected.model_dump(mode="json")
    with queue._bind(handler), queue._event_loop(handler):
        queue._actor.fn(payload)

    assert handler.received == [expected]
    assert handler.failed == []
    delayed = Message.decode(stub_broker.queues[dq_name("parse")].get_nowait())
    assert delayed.args == (payload,)
    assert delayed.options.get("retries", 0) == 0
    assert delayed.options["deferrals"] == 1
    remaining_ms = delayed.options["eta"] - current_millis()
    assert 29_000 <= remaining_ms <= 60_000


def test_a_later_deferral_waits_longer_without_inheriting_retries(
    stub_broker: StubBroker,
) -> None:
    handler = _Recorder(raises=JobDeferred("vLLM is not answering"))
    queue = _queue(stub_broker, max_retries=0)
    payload = _message().model_dump(mode="json")
    started = current_millis() - 60_000
    current = queue._actor.message_with_options(
        args=(payload,),
        deferred_since=started,
        deferrals=1,
        retries=2,
    )
    token = CurrentMessage._MESSAGE.set(current)
    try:
        with queue._bind(handler), queue._event_loop(handler):
            queue._actor.fn(payload)
    finally:
        CurrentMessage._MESSAGE.reset(token)

    delayed = Message.decode(stub_broker.queues[dq_name("parse")].get_nowait())
    assert delayed.options["deferred_since"] == started
    assert delayed.options["deferrals"] == 2
    assert delayed.options.get("retries", 0) == 0
    remaining_ms = delayed.options["eta"] - current_millis()
    assert 59_000 <= remaining_ms <= 120_000


def test_a_deferral_past_the_deadline_is_not_sent_again(stub_broker: StubBroker) -> None:
    handler = _Recorder(raises=JobDeferred("vLLM is not answering"))
    queue = _queue(stub_broker, max_retries=0)
    expected = _message()
    payload = expected.model_dump(mode="json")
    current = queue._actor.message_with_options(
        args=(payload,),
        deferred_since=current_millis() - 3_600_000,
        deferrals=4,
    )
    token = CurrentMessage._MESSAGE.set(current)
    try:
        with queue._bind(handler), queue._event_loop(handler):
            queue._actor.fn(payload)
    finally:
        CurrentMessage._MESSAGE.reset(token)

    assert handler.received == [expected]
    assert len(handler.failed) == 1
    assert handler.failed[0][0] == expected
    assert isinstance(handler.failed[0][1], JobDeferralExpired)
    with pytest.raises(Empty):
        stub_broker.queues[dq_name("parse")].get_nowait()


def test_a_failed_attempt_starts_a_new_deferral_if_it_defers_later(stub_broker: StubBroker) -> None:
    class _ThenDefer(_Recorder):
        def __init__(self) -> None:
            super().__init__()
            self._calls = 0

        async def handle(self, message: ParseJobMessage) -> None:
            self.received.append(message)
            self._calls += 1
            if self._calls == 1:
                raise RuntimeError("extract failed")
            raise JobDeferred("vLLM is not answering")

    handler = _ThenDefer()
    queue = _queue(stub_broker, max_retries=1)
    payload = _message().model_dump(mode="json")
    current = queue._actor.message_with_options(
        args=(payload,),
        deferred_since=current_millis() - 3_600_000,
        deferrals=4,
    )
    token = CurrentMessage._MESSAGE.set(current)
    try:
        with queue._bind(handler), queue._event_loop(handler):
            with pytest.raises(RuntimeError):
                queue._actor.fn(payload)
            assert "deferred_since" not in current.options
            assert "deferrals" not in current.options
            queue._actor.fn(payload)
    finally:
        CurrentMessage._MESSAGE.reset(token)

    assert handler.failed == []
    delayed = Message.decode(stub_broker.queues[dq_name("parse")].get_nowait())
    assert delayed.options["deferrals"] == 1
    assert delayed.options["deferred_since"] >= current_millis() - 5_000


def test_a_retry_of_an_attempt_drops_the_deferral_clock(stub_broker: StubBroker) -> None:
    queue = _queue(stub_broker, max_retries=1, min_backoff_ms=1)
    payload = _message().model_dump(mode="json")
    current = queue._actor.message_with_options(
        args=(payload,),
        deferred_since=current_millis() - 3_600_000,
        deferrals=4,
    )
    token = CurrentMessage._MESSAGE.set(current)
    try:
        handler = _Recorder(raises=RuntimeError("extract failed"))
        with (
            queue._bind(handler),
            queue._event_loop(handler),
            pytest.raises(RuntimeError),
        ):
            queue._actor.fn(payload)
        # Dramatiq retries by enqueueing this same message, so the cleared options travel with it.
        Retries().after_process_message(  # type: ignore[no-untyped-call]
            stub_broker, current, exception=RuntimeError("extract failed")
        )
    finally:
        CurrentMessage._MESSAGE.reset(token)

    delayed = Message.decode(stub_broker.queues[dq_name("parse")].get_nowait())
    assert "deferred_since" not in delayed.options
    assert "deferrals" not in delayed.options
    assert delayed.options["retries"] == 1


def test_handler_failure_calls_on_failed_after_retries(stub_broker: StubBroker) -> None:
    handler = _Recorder(raises=RuntimeError("parse failed"))
    queue = _queue(stub_broker, max_retries=2, min_backoff_ms=1)
    expected = _message()
    _submit(queue, expected)
    queue.consume(handler, until_empty=True)
    assert handler.received == [expected] * 3
    assert len(handler.failed) == 1
    assert handler.failed[0][0] == expected
    assert isinstance(handler.failed[0][1], RuntimeError)


def test_time_limit_calls_on_failed_after_retries(stub_broker: StubBroker) -> None:
    handler = _Recorder(raises=JobTimeLimitExceeded())
    queue = _queue(stub_broker, max_retries=0)
    expected = _message()
    _submit(queue, expected)
    queue.consume(handler, until_empty=True)
    assert len(handler.failed) == 1
    assert handler.failed[0][0] == expected
    assert isinstance(handler.failed[0][1], JobTimeLimitExceeded)


class _Blocking(JobHandler[ParseJobMessage]):
    """A parse that sits in a thread longer than the time limit."""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self.cancelled = Event()
        self.calls = 0
        self.started_at: list[float] = []
        self.finished_at: float | None = None
        self.cleaned_at: float | None = None
        self.failed: list[BaseException] = []

    async def handle(self, message: ParseJobMessage) -> None:
        self.calls += 1
        call = self.calls
        self.started_at.append(time.monotonic())
        try:
            await asyncio.to_thread(self._block, call)
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        finally:
            await asyncio.to_thread(self._clean, call)

    def _block(self, call: int) -> None:
        if call > 1:
            return
        time.sleep(self.seconds)
        self.finished_at = time.monotonic()

    def _clean(self, call: int) -> None:
        if call == 1:
            self.cleaned_at = time.monotonic()

    async def on_failed(self, message: ParseJobMessage, exc: BaseException) -> None:
        self.failed.append(exc)


def test_consume_enforces_the_time_limit(stub_broker: StubBroker) -> None:
    """The broker is not booted by the test: consume must do what Dramatiq's CLI would.

    The blocking call runs past the limit. consume returns only after that call
    and the handler's cleanup have returned, so the next job cannot start while
    either is still running.
    """
    (time_limit,) = [m for m in stub_broker.middleware if isinstance(m, TimeLimit)]
    time_limit.manager.interval = 0.05  # type: ignore[union-attr]
    # The call runs past the limit, and returns within the second limit. A call that
    # is still going after that exits the process.
    queue = _queue(stub_broker, time_limit_ms=1000, max_retries=0)
    handler = _Blocking(1.2)
    _submit(queue, _message())
    _submit(queue, _message("3f2b8c1e-9a4d-4f6b-8c2e-1d5a7b9c0e35"))

    queue.consume(handler, until_empty=True)

    assert handler.cancelled.is_set()
    assert handler.calls == 2
    assert handler.finished_at is not None
    assert handler.cleaned_at is not None
    assert handler.cleaned_at >= handler.finished_at
    assert handler.started_at[1] >= handler.cleaned_at
    assert [type(exc) for exc in handler.failed] == [JobTimeLimitExceeded]


class _Absorbing(JobHandler[ParseJobMessage]):
    """Takes the cancellation and returns, as the parse handler does for a permanent failure."""

    def __init__(self) -> None:
        self.calls = 0
        self.failed: list[BaseException] = []

    async def handle(self, message: ParseJobMessage) -> None:
        self.calls += 1
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            return

    async def on_failed(self, message: ParseJobMessage, exc: BaseException) -> None:
        self.failed.append(exc)


def test_a_handler_that_returns_after_the_time_limit_is_not_retried(
    stub_broker: StubBroker,
) -> None:
    (time_limit,) = [m for m in stub_broker.middleware if isinstance(m, TimeLimit)]
    time_limit.manager.interval = 0.05  # type: ignore[union-attr]
    queue = _queue(stub_broker, time_limit_ms=300, max_retries=3, min_backoff_ms=1)
    handler = _Absorbing()
    _submit(queue, _message())

    queue.consume(handler, until_empty=True)

    assert handler.calls == 1
    assert handler.failed == []


def test_two_queues_in_one_process_consume_without_sharing_a_loop(
    stub_broker: StubBroker,
) -> None:
    first = _queue(stub_broker, name="first")
    second = _queue(stub_broker, name="second")
    seen: dict[str, asyncio.AbstractEventLoop] = {}
    inner_done = Event()

    class _Inner(JobHandler[ParseJobMessage]):
        async def handle(self, message: ParseJobMessage) -> None:
            seen["second"] = asyncio.get_running_loop()
            inner_done.set()

    class _Outer(JobHandler[ParseJobMessage]):
        async def handle(self, message: ParseJobMessage) -> None:
            seen["first"] = asyncio.get_running_loop()
            # Run the second consumer to completion while this handler is still running.
            await asyncio.to_thread(second.consume, _Inner(), until_empty=True)
            assert inner_done.is_set()
            # Closing the second consumer must leave this queue's loop in place.
            await asyncio.to_thread(lambda: None)

    _submit(first, _message())
    _submit(second, _message())

    first.consume(_Outer(), until_empty=True)

    assert seen["first"] is not seen["second"]


def test_a_blocking_call_that_never_returns_exits_the_process(
    stub_broker: StubBroker, monkeypatch: pytest.MonkeyPatch
) -> None:
    (time_limit,) = [m for m in stub_broker.middleware if isinstance(m, TimeLimit)]
    time_limit.manager.interval = 0.05  # type: ignore[union-attr]
    release = Event()
    exits: list[int] = []

    def fake_exit(code: int) -> None:
        exits.append(code)
        release.set()

    monkeypatch.setattr("os._exit", fake_exit)

    class _Stuck(JobHandler[ParseJobMessage]):
        async def handle(self, message: ParseJobMessage) -> None:
            await asyncio.to_thread(self._block)

        def _block(self) -> None:
            while not release.wait(timeout=0.05):
                pass

    queue = _queue(stub_broker, time_limit_ms=200, max_retries=0)
    _submit(queue, _message())

    queue.consume(_Stuck(), until_empty=True)

    assert exits == [1]
    # The delivery is settled, so it is not retried and not dead-lettered.
    assert list(stub_broker.dead_letters) == []


def test_the_backstop_adds_at_most_60_seconds(stub_broker: StubBroker) -> None:
    """A long limit does not get a second full limit. A short one still doubles."""
    long = _queue(stub_broker, name="long", time_limit_ms=1_800_000)
    short = _queue(stub_broker, name="short", time_limit_ms=1_000)

    assert long._delivery_bound_s() == 1_860
    assert short._delivery_bound_s() == 2


def test_a_blocking_call_that_never_returns_is_not_retried(
    stub_broker: StubBroker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stuck call is a final failure even when retries remain."""
    (time_limit,) = [m for m in stub_broker.middleware if isinstance(m, TimeLimit)]
    time_limit.manager.interval = 0.05  # type: ignore[union-attr]
    release = Event()
    exits: list[int] = []
    calls = 0
    calls_at_exit: list[int] = []
    waiting_at_exit: list[int] = []
    reported: list[BaseException] = []

    def fake_exit(code: int) -> None:
        waiting_at_exit.append(stub_broker.queues[dq_name("parse")].qsize())
        calls_at_exit.append(calls)
        exits.append(code)
        release.set()

    monkeypatch.setattr("os._exit", fake_exit)

    class _Stuck(JobHandler[ParseJobMessage]):
        async def handle(self, message: ParseJobMessage) -> None:
            nonlocal calls
            calls += 1
            await asyncio.to_thread(release.wait)

        async def on_failed(self, message: ParseJobMessage, exc: BaseException) -> None:
            reported.append(exc)

    queue = _queue(stub_broker, time_limit_ms=200, max_retries=1, min_backoff_ms=1)
    _submit(queue, _message())

    queue.consume(_Stuck(), until_empty=True)

    assert calls_at_exit == [1]
    assert exits == [1]
    assert waiting_at_exit == [0]
    assert [type(exc) for exc in reported] == [JobTimeLimitExceeded]


def test_a_stuck_last_attempt_is_reported_before_the_process_exits(
    stub_broker: StubBroker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """on_failed gets to finish: the job is marked failed before the exit."""
    (time_limit,) = [m for m in stub_broker.middleware if isinstance(m, TimeLimit)]
    time_limit.manager.interval = 0.05  # type: ignore[union-attr]
    release = Event()
    reported: list[BaseException] = []
    reported_at_exit: list[int] = []

    def fake_exit(code: int) -> None:
        reported_at_exit.append(len(reported))
        release.set()

    monkeypatch.setattr("os._exit", fake_exit)

    class _Stuck(JobHandler[ParseJobMessage]):
        async def handle(self, message: ParseJobMessage) -> None:
            await asyncio.to_thread(release.wait)

        async def on_failed(self, message: ParseJobMessage, exc: BaseException) -> None:
            await asyncio.sleep(0.05)
            reported.append(exc)

    queue = _queue(stub_broker, time_limit_ms=200, max_retries=0)
    _submit(queue, _message())

    queue.consume(_Stuck(), until_empty=True)

    assert reported_at_exit == [1]
    assert [type(exc) for exc in reported] == [JobTimeLimitExceeded]


def test_a_stuck_blocking_call_does_not_block_the_failure_hook(
    stub_broker: StubBroker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """on_failed runs on a new thread. The stuck parse thread cannot be killed."""
    (time_limit,) = [m for m in stub_broker.middleware if isinstance(m, TimeLimit)]
    time_limit.manager.interval = 0.05  # type: ignore[union-attr]
    release = Event()
    reported: list[BaseException] = []

    def fake_exit(code: int) -> None:
        release.set()

    monkeypatch.setattr("os._exit", fake_exit)

    class _Stuck(JobHandler[ParseJobMessage]):
        async def handle(self, message: ParseJobMessage) -> None:
            await asyncio.to_thread(release.wait)

        async def on_failed(self, message: ParseJobMessage, exc: BaseException) -> None:
            await asyncio.to_thread(reported.append, exc)

    queue = _queue(stub_broker, time_limit_ms=200, max_retries=0)
    _submit(queue, _message())

    queue.consume(_Stuck(), until_empty=True)

    assert [type(exc) for exc in reported] == [JobTimeLimitExceeded]


def test_cleanup_queued_behind_a_stuck_call_still_runs_the_hook(
    stub_broker: StubBroker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The handler's finally waits on the same thread, as parse_pdf_bytes does when it unlinks.

    The task never finishes, so the pool has to be replaced before on_failed or the hook's
    own blocking call waits behind the stuck one.
    """
    (time_limit,) = [m for m in stub_broker.middleware if isinstance(m, TimeLimit)]
    time_limit.manager.interval = 0.05  # type: ignore[union-attr]
    release = Event()
    reported: list[BaseException] = []
    reported_at_exit: list[BaseException] = []

    def fake_exit(code: int) -> None:
        reported_at_exit.extend(reported)
        release.set()

    monkeypatch.setattr("os._exit", fake_exit)

    class _Stuck(JobHandler[ParseJobMessage]):
        async def handle(self, message: ParseJobMessage) -> None:
            try:
                await asyncio.to_thread(release.wait)
            finally:
                await asyncio.to_thread(release.wait)

        async def on_failed(self, message: ParseJobMessage, exc: BaseException) -> None:
            await asyncio.to_thread(reported.append, exc)

    queue = _queue(stub_broker, time_limit_ms=200, max_retries=0)
    _submit(queue, _message())

    queue.consume(_Stuck(), until_empty=True)

    assert [type(exc) for exc in reported_at_exit] == [JobTimeLimitExceeded]


def test_an_error_raised_after_cancel_is_the_failure(stub_broker: StubBroker) -> None:
    """A handler that raises out of the cancellation is retried as that error."""
    (time_limit,) = [m for m in stub_broker.middleware if isinstance(m, TimeLimit)]
    time_limit.manager.interval = 0.05  # type: ignore[union-attr]
    seen: list[BaseException] = []

    class _Raises(JobHandler[ParseJobMessage]):
        async def handle(self, message: ParseJobMessage) -> None:
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                raise RuntimeError("cleanup failed") from None

        async def on_failed(self, message: ParseJobMessage, exc: BaseException) -> None:
            seen.append(exc)

    queue = _queue(stub_broker, time_limit_ms=200, max_retries=0)
    _submit(queue, _message())

    queue.consume(_Raises(), until_empty=True)

    assert [type(exc) for exc in seen] == [RuntimeError]
    assert str(seen[0]) == "cleanup failed"


def test_a_call_that_returned_in_time_is_not_marked_stuck() -> None:
    guard = DeliveryGuard("parse")
    with guard.watch(60, count_attempt=True) as watch:
        pass

    watch.expire()

    assert not watch.stuck.is_set()
    assert not guard.exit_after_ack.is_set()


def test_a_stuck_delivery_on_one_queue_does_not_exit_for_another(
    stub_broker: StubBroker, monkeypatch: pytest.MonkeyPatch
) -> None:
    exits: list[int] = []
    monkeypatch.setattr("os._exit", exits.append)
    first = _queue(stub_broker, name="first")
    second = _queue(stub_broker, name="second")
    first._guard.exit_after_ack.set()
    handler = _Recorder()
    _submit(second, _message())

    second.consume(handler, until_empty=True)

    assert exits == []
    assert handler.received == [_message()]


def test_a_hook_that_never_returns_exits_the_process(
    stub_broker: StubBroker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """on_failed has no second time-limit injection, so its own bound exits the process."""
    release = Event()
    exits: list[int] = []

    def fake_exit(code: int) -> None:
        exits.append(code)
        release.set()

    monkeypatch.setattr("os._exit", fake_exit)
    # This hook never settles, so the real 30 s wait would hold the test.
    monkeypatch.setattr("bioparser.jobqueue.dramatiq.watchdog._ACK_WAIT_S", 0.2)

    class _StuckHook(JobHandler[ParseJobMessage]):
        async def handle(self, message: ParseJobMessage) -> None:
            raise RuntimeError("parse failed")

        async def on_failed(self, message: ParseJobMessage, exc: BaseException) -> None:
            await asyncio.to_thread(release.wait)

    queue = _queue(stub_broker, time_limit_ms=200, max_retries=0)
    _submit(queue, _message())

    queue.consume(_StuckHook(), until_empty=True)

    assert exits == [1]


def test_shutdown_that_never_returns_exits_the_process(
    stub_broker: StubBroker, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = Event()
    exits: list[int] = []

    def fake_exit(code: int) -> None:
        exits.append(code)
        release.set()

    monkeypatch.setattr("os._exit", fake_exit)

    class _StuckClose(JobHandler[ParseJobMessage]):
        async def handle(self, message: ParseJobMessage) -> None:
            return

        async def aclose(self) -> None:
            await asyncio.to_thread(release.wait)

    queue = _queue(stub_broker, time_limit_ms=200)
    queue.consume(_StuckClose(), until_empty=True)

    assert exits == [1]


def test_consume_can_run_more_than_once_on_one_broker(stub_broker: StubBroker) -> None:
    """The boot event starts a thread, and a thread starts only once."""
    queue = _queue(stub_broker)
    queue.consume(_Recorder(), until_empty=True)

    queue.consume(_Recorder(), until_empty=True)


def test_redis_broker_has_the_middleware_the_queue_relies_on() -> None:
    broker = _create_redis_broker("redis://localhost:6379/0")

    kinds = {type(middleware) for middleware in broker.middleware}
    DramatiqJobConsumer(name="parse", model=ParseJobMessage, broker=broker)

    assert {TimeLimit, Retries} <= kinds
    assert CurrentMessage in {type(middleware) for middleware in broker.middleware}


def test_submitter_leaves_retries_and_time_limit_to_the_consumer(
    stub_broker: StubBroker,
) -> None:
    (time_limit,) = [m for m in stub_broker.middleware if isinstance(m, TimeLimit)]
    manager = time_limit.manager
    submitter = DramatiqJobSubmitter(name="extract", broker=stub_broker)
    with pytest.raises(ActorNotFound):
        stub_broker.get_actor("extract")
    _submit(submitter, _message())

    assert time_limit.manager is manager
    message = Message.decode(stub_broker.queues["extract"].queue[0])
    assert message.actor_name == "extract"
    assert "max_retries" not in message.options
    assert "time_limit" not in message.options

    handler = _Recorder()
    _queue(stub_broker, name="extract").consume(handler, until_empty=True)
    assert handler.received == [_message()]


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
        async def handle(self, message: ParseJobMessage) -> None:
            started.set()
            await asyncio.to_thread(time.sleep, 0.3)
            finished.set()

    stop = Event()
    queue = _queue(stub_broker)
    _submit(queue, _message())

    def stop_once_started() -> None:
        started.wait(5)
        stop.set()

    threading.Thread(target=stop_once_started, daemon=True).start()

    queue.consume(_Slow(), stop=stop)

    assert finished.is_set()


def test_several_queues_on_one_broker_share_one_job_time_limit(stub_broker: StubBroker) -> None:
    _queue(stub_broker, name="first")
    (time_limit,) = [m for m in stub_broker.middleware if isinstance(m, TimeLimit)]
    manager = time_limit.manager

    _queue(stub_broker, name="second")

    assert time_limit.manager is manager


def test_process_boot_after_the_queue_is_built_leaves_the_timer_running() -> None:
    """Dramatiq's CLI emits process_boot, which starts the manager a second time."""
    broker = StubBroker()
    try:
        DramatiqJobConsumer(name="parse", model=ParseJobMessage, broker=broker)
        (time_limit,) = [m for m in broker.middleware if isinstance(m, TimeLimit)]
        manager = time_limit.manager

        broker.emit_after("process_boot")

        assert time_limit.manager is manager
        assert cast(Any, manager).is_alive()
    finally:
        broker.close()


def test_queue_keeps_a_time_limit_that_is_already_running() -> None:
    """The timer raises Dramatiq's own type. The queue translates it, so it can already be running."""
    broker = StubBroker()
    broker.emit_after("process_boot")
    try:
        (time_limit,) = [m for m in broker.middleware if isinstance(m, TimeLimit)]
        manager = time_limit.manager
        DramatiqJobConsumer(name="parse", model=ParseJobMessage, broker=broker)
        assert time_limit.manager is manager
        assert cast(Any, manager).is_alive()
    finally:
        broker.close()


def test_queue_refuses_a_broker_without_a_time_limit_middleware() -> None:
    broker = StubBroker(middleware=[])

    with pytest.raises(JobQueueConfigError, match="TimeLimit"):
        DramatiqJobConsumer(name="parse", model=ParseJobMessage, broker=broker)
    broker.close()
