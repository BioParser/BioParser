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
