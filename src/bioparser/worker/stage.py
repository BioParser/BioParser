"""Shared claim and failure handling for one queue stage.

The queue handler is synchronous. Job state is async, so each handler keeps one
event loop and runs each state call to completion on it. What the stage does
after it has claimed the job, and where it records the outcome, is the
subclass's `run`.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Awaitable
from typing import Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncConnection

from bioparser.db import engine as database
from bioparser.db import jobs as job_queries
from bioparser.jobqueue import JobTimeLimitExceeded
from bioparser.jobstate import (
    CorruptJobStateError,
    JobNotFoundError,
    JobState,
    JobStatus,
    SafeErrorCode,
)
from bioparser.logging_config import log_context

LOGGER = logging.getLogger(__name__)


class JobMessage(Protocol):
    """A queue message that names one stored job."""

    job_id: UUID


class PermanentJobFailure(Exception):
    """A job error that must be recorded and not retried."""

    def __init__(self, code: SafeErrorCode) -> None:
        self.code = code
        super().__init__(code)


class StageJobHandler[T: JobMessage]:
    """Claim one stage, then let the subclass finish the job.

    A status outside `owned` is left unchanged. A `PermanentJobFailure`
    from `run` is recorded as a safe error. Any other exception is retried.
    `ready` may raise `JobDeferred` before the claim. The queue sends the
    message again later without using a retry, and gives up at a deadline.
    Any other result drops that outage's clock, so a retry of this message
    starts a new hour if it has to defer.
    `on_failed` then marks the job failed when its status allows it.
    """

    def __init__(
        self,
        *,
        database_url: str,
        owned: frozenset[JobStatus],
        claim_status: JobStatus,
        max_claims: int,
    ) -> None:
        self._database_url = database_url
        self._owned = owned
        self._claim_status = claim_status
        self._max_claims = max_claims
        self._loop = asyncio.new_event_loop()
        # One thread per worker process is the supported setup (`worker_threads=1` in each
        # runtime). The loop and the process-wide pool in `bioparser.db.engine` belong to
        # that one thread. This lock keeps extra threads correct, not fast: they would
        # queue behind a running stage, including its blocking parse or extract work.
        # Scale with more worker replicas, not threads.
        self._loop_lock = threading.Lock()

    def close(self) -> None:
        if self._loop.is_closed():
            return
        try:
            self.run_async(database.aclose())
        finally:
            self._loop.close()

    def run_async[R](self, awaitable: Awaitable[R]) -> R:
        """Run one awaitable to completion on the shared loop.

        The queue can raise JobTimeLimitExceeded in this thread at any point,
        including while the loop is running. The interrupted task would stay
        on the loop and resume during the next call, so it is cancelled and
        drained before the exception leaves.
        """
        with self._loop_lock:
            task = asyncio.ensure_future(awaitable, loop=self._loop)
            try:
                return self._loop.run_until_complete(task)
            except BaseException:
                task.cancel()
                self._loop.run_until_complete(asyncio.gather(task, return_exceptions=True))
                raise

    def handle(self, message: T) -> None:
        with log_context(job_id=str(message.job_id)):
            self.run_async(self._handle(message.job_id))

    def on_malformed(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        try:
            job_id = UUID(payload["job_id"])
        except (KeyError, TypeError, ValueError, AttributeError):
            # UUID() raises AttributeError, not TypeError, for a non-string such as an int.
            return
        with log_context(job_id=str(job_id)):
            LOGGER.error(
                "queue message is malformed and can never be processed. "
                "Marking the job failed (internal_error)"
            )
            self.run_async(self._mark_failed(job_id, "internal_error"))

    def on_failed(self, message: T, exc: BaseException) -> None:
        code: SafeErrorCode = (
            "timeout" if isinstance(exc, JobTimeLimitExceeded) else "internal_error"
        )
        with log_context(job_id=str(message.job_id)):
            recorded = self.run_async(self._mark_failed(message.job_id, code))
            if recorded:
                LOGGER.error(
                    f"giving up after {type(exc).__name__}. Marked the job failed ({code})"
                )
            else:
                LOGGER.info(
                    f"giving up after {type(exc).__name__}. Not marking the job failed ({code})"
                )

    async def ready(self) -> None:
        """Raise JobDeferred when a dependency is down. The job is unchanged."""
        return

    async def run(self, state: JobState) -> None:
        """Do the stage's work. The claim is already stored.

        `state` was read before the claim. The claim changes the status and that
        stage's count. A later write sets only the columns for its outcome.
        """
        raise NotImplementedError

    async def _open(self) -> None:
        await database.open(self._database_url)

    async def _handle(self, job_id: UUID) -> None:
        await self._open()
        async with database.transaction() as connection:
            try:
                state = await job_queries.get(connection, job_id)
            except CorruptJobStateError:
                LOGGER.error(
                    "stored job state is unreadable. Dropping the message without retry, "
                    "the job stays in its stored status"
                )
                return
            if state is None:
                raise JobNotFoundError(f"no stored state for job {job_id}. Message will be retried")
            if state.status not in self._owned:
                LOGGER.info(f"status is already {state.status!r}. Skipping this delivery")
                return
        await self.ready()
        if not await self._mark_running(state):
            return
        try:
            await self.run(state)
        except PermanentJobFailure as exc:
            LOGGER.error(f"stage failed. Marking the job failed ({exc.code})")
            await self._mark_failed(job_id, exc.code)

    async def _mark_running(self, state: JobState) -> bool:
        claim = self._claim_status
        async with database.transaction() as connection:
            outcome = await job_queries.claim(
                connection,
                state.job_id,
                status=claim,
                from_statuses=self._owned,
                max_claims=self._max_claims,
            )
        if outcome == "claimed":
            return True
        if outcome == "exhausted":
            LOGGER.error(
                f"{claim} was already claimed {self._max_claims} times. "
                "Marked the job failed (internal_error)"
            )
            return False
        if outcome == "missing":
            LOGGER.info(f"no stored job state exists. Skipping this delivery before {claim}")
            return False
        LOGGER.info(f"job moved on before this delivery could start {claim}. Skipping")
        return False

    async def _mark_failed(self, job_id: UUID, code: SafeErrorCode) -> bool:
        """Record `failed` when this stage still owns the job. True when the write lands."""
        await self._open()
        async with database.transaction() as connection:
            return await self._mark_failed_in(connection, job_id, code)

    async def _mark_failed_in(
        self,
        connection: AsyncConnection,
        job_id: UUID,
        code: SafeErrorCode,
    ) -> bool:
        outcome = await job_queries.mark_failed(connection, job_id, code, from_statuses=self._owned)
        if outcome == "recorded":
            return True
        if outcome == "not_allowed":
            LOGGER.error("job cannot be marked failed.")
            return False
        if outcome == "missing":
            LOGGER.info(f"no stored job state exists. Not marking it failed ({code})")
            return False
        LOGGER.info(f"job already finished or moved on. Not marking it failed ({code})")
        return False
