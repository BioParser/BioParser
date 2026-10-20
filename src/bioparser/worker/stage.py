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

from bioparser.jobqueue import JobTimeLimitExceeded
from bioparser.jobstate import (
    ALLOWED_TRANSITIONS,
    JobNotFoundError,
    JobState,
    JobStateStore,
    JobStatus,
    SafeError,
    SafeErrorCode,
    StaleJobStateWriteError,
)
from bioparser.logging_config import log_context

from .job import CorruptJobRecord, MissingJobRecord, fetch_job

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

    Statuses outside `owned` are left to `on_outside`. A `PermanentJobFailure`
    from `run` is recorded as a safe error. Any other exception is retried.
    `ready` may raise `JobDeferred` before the claim, which sends the message
    again later without using a retry.
    """

    def __init__(
        self,
        *,
        jobs: JobStateStore,
        owned: frozenset[JobStatus],
        claim_status: JobStatus,
        max_claims: int,
    ) -> None:
        self._jobs = jobs
        self._owned = owned
        self._claim_status = claim_status
        self._max_claims = max_claims
        self._loop = asyncio.new_event_loop()
        # One loop serves every callback, so calls from different threads take turns.
        self._loop_lock = threading.Lock()

    def close(self) -> None:
        if self._loop.is_closed():
            return
        try:
            self.run_async(self._jobs.aclose())
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

        `state` is the job from before the claim. Read it again before writing
        a result: `update` replaces the whole record, so a write from `state`
        would drop the claim count just stored.
        """
        raise NotImplementedError

    def on_outside(self, state: JobState) -> None:
        """The job is not in this stage. The delivery does not change it."""
        return

    async def _handle(self, job_id: UUID) -> None:
        match await fetch_job(self._jobs, job_id):
            case CorruptJobRecord():
                LOGGER.error(
                    "stored job state is unreadable. Dropping the message without retry, "
                    "the job stays in its stored status"
                )
                return
            case MissingJobRecord():
                raise JobNotFoundError(f"no stored state for job {job_id}. Message will be retried")
            case JobState() as state:
                pass
        if state.status not in self._owned:
            LOGGER.info(f"status is already {state.status!r}. Skipping this delivery")
            self.on_outside(state)
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
        if state.claims(claim) >= self._max_claims:
            LOGGER.error(
                f"{claim} was already claimed {state.claims(claim)} times. "
                "Marking the job failed (internal_error)"
            )
            await self._mark_failed(state.job_id, "internal_error")
            return False
        try:
            await self._jobs.update(state.with_claim(claim))
        except StaleJobStateWriteError:
            LOGGER.info(f"job moved on before this delivery could start {claim}. Skipping")
            return False
        return True

    async def _mark_failed(self, job_id: UUID, code: SafeErrorCode) -> bool:
        """Record `failed` when this stage still owns the job. True when the write lands."""
        match await fetch_job(self._jobs, job_id):
            case CorruptJobRecord():
                LOGGER.error("stored job state is unreadable. Cannot mark it failed")
                return False
            case MissingJobRecord():
                LOGGER.error("no stored job state exists. Cannot mark it failed")
                return False
            case JobState() as state:
                pass
        if state.status not in self._owned:
            return False
        if "failed" not in ALLOWED_TRANSITIONS[state.status]:
            LOGGER.error(
                f"job is {state.status!r}, which cannot be marked failed. "
                "It waits for another delivery"
            )
            return False
        try:
            await self._jobs.update(state.with_changes(status="failed", error=SafeError(code=code)))
        except StaleJobStateWriteError:
            LOGGER.info(f"job already finished or moved on. Not marking it failed ({code})")
            return False
        return True
