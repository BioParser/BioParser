import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from redis.exceptions import TimeoutError as RedisTimeoutError

from bioparser.jobstate.errors import JobStateBackendError, JobStateConnectionError, JobStateError
from bioparser.jobstate.redis_store import RedisJobStateStore, _call_with_redis_errors


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# --- _run_with_redis_errors -------------------------------------------------


@pytest.mark.anyio
async def test_retries_timeout_then_succeeds() -> None:
    attempts = 0

    async def flaky() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RedisTimeoutError("simulated")
        return "ok"

    assert await _call_with_redis_errors(flaky) == "ok"
    assert attempts == 3


@pytest.mark.anyio
async def test_exhausting_retries_raises_connection_error() -> None:
    async def always_times_out() -> str:
        raise RedisTimeoutError("simulated")

    with pytest.raises(JobStateConnectionError):
        await _call_with_redis_errors(always_times_out)


@pytest.mark.anyio
async def test_connection_error_is_not_retried() -> None:
    attempts = 0

    async def always_refuses() -> str:
        nonlocal attempts
        attempts += 1
        raise RedisConnectionError("simulated")

    with pytest.raises(JobStateConnectionError):
        await _call_with_redis_errors(always_refuses)
    assert attempts == 1


@pytest.mark.anyio
async def test_other_redis_error_raises_backend_error_without_retry() -> None:
    attempts = 0

    async def rejected() -> str:
        nonlocal attempts
        attempts += 1
        raise RedisError("OOM command not allowed")

    with pytest.raises(JobStateBackendError):
        await _call_with_redis_errors(rejected)
    assert attempts == 1


@pytest.mark.anyio
async def test_retry_timeout_false_does_not_retry() -> None:
    attempts = 0

    async def times_out_once() -> str:
        nonlocal attempts
        attempts += 1
        raise RedisTimeoutError("simulated")

    with pytest.raises(JobStateConnectionError):
        await _call_with_redis_errors(times_out_once, retry_timeout=False)
    assert attempts == 1


# --- constructor validation --------------------------------------------------


def test_non_positive_ttl_seconds_rejected() -> None:
    with pytest.raises(ValueError, match="ttl_seconds"):
        RedisJobStateStore("redis://localhost:6379/0", ttl_seconds=0)


def test_non_positive_operation_timeout_rejected() -> None:
    with pytest.raises(ValueError, match="operation_timeout_seconds"):
        RedisJobStateStore("redis://localhost:6379/0", operation_timeout_seconds=0)


def test_malformed_redis_url_raises_job_state_error() -> None:
    with pytest.raises(JobStateError):
        RedisJobStateStore("not-a-valid-url")


# --- error messages hold the Redis error type, not its text (#140) ------------

PLANTED_URL = "redis://:planted-secret@cache.internal:6380/0"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("redis_error", "expected"),
    [
        (RedisTimeoutError(f"Timeout connecting to {PLANTED_URL}"), JobStateConnectionError),
        (RedisConnectionError(f"Error 111 connecting to {PLANTED_URL}"), JobStateConnectionError),
        (RedisError(f"NOPERM no permissions on {PLANTED_URL}"), JobStateBackendError),
    ],
)
async def test_error_message_names_redis_error_type_but_not_its_text(
    redis_error: RedisError, expected: type[JobStateError]
) -> None:
    async def fails() -> str:
        raise redis_error

    with pytest.raises(expected) as exc_info:
        await _call_with_redis_errors(fails, retry_timeout=False)

    message = str(exc_info.value)
    assert type(redis_error).__name__ in message
    assert "planted-secret" not in message
    assert "cache.internal" not in message
    assert exc_info.value.__cause__ is redis_error


def test_bad_redis_url_error_holds_neither_the_url_nor_the_value_error_text() -> None:
    # An unescaped "/" in the password ends the host part early, so redis-py's
    # port error quotes the start of the password.
    url = "redis://:planted/secret@cache.internal:6379/0"

    with pytest.raises(JobStateError) as exc_info:
        RedisJobStateStore(url)

    cause = exc_info.value.__cause__
    assert isinstance(cause, ValueError)
    assert "planted" in str(cause)  # the text that used to be copied
    message = str(exc_info.value)
    assert message == "invalid Redis URL"
    assert str(cause) not in message
