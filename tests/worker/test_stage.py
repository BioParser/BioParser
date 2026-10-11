import asyncio
import ctypes
import threading
from uuid import UUID

import pytest
from pydantic import BaseModel

from bioparser.jobqueue import JobTimeLimitExceeded
from bioparser.jobstate import JobState, JobStatus
from bioparser.worker.stage import StageJobHandler


class _Message(BaseModel):
    job_id: UUID = UUID("3f2b8c1e-9a4d-4f6b-8c2e-1d5a7b9c0e34")


class _Jobs:
    async def aclose(self) -> None:
        return


class _Idle(StageJobHandler[_Message]):
    def __init__(self) -> None:
        super().__init__(
            jobs=_Jobs(),  # type: ignore[arg-type]
            owned=frozenset[JobStatus](("queued",)),
            claim_status="parsing",
            max_claims=1,
        )

    async def run(self, state: JobState) -> None:
        raise NotImplementedError


def test_interrupted_async_call_is_cancelled_not_resumed() -> None:
    """A JobTimeLimitExceeded mid-await must not leave a task that resumes on the next call."""
    handler = _Idle()
    resumed: list[str] = []

    async def slow() -> None:
        try:
            while True:
                await asyncio.sleep(0.02)
        finally:
            resumed.append("cleanup")

    caller = threading.get_ident()
    timer = threading.Timer(
        0.2,
        lambda: ctypes.pythonapi.PyThreadState_SetAsyncExc(
            ctypes.c_ulong(caller), ctypes.py_object(JobTimeLimitExceeded)
        ),
    )
    timer.start()
    with pytest.raises(JobTimeLimitExceeded):
        handler.run_async(slow())
    timer.join()
    assert handler.run_async(asyncio.sleep(0, result="next")) == "next"
    assert resumed == ["cleanup"]
    assert not asyncio.all_tasks(handler._loop)
    handler.close()
