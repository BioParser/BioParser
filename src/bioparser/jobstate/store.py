from typing import Protocol
from uuid import UUID

from .models import JobState


class JobStateStore(Protocol):
    """Replaceable interface for creating, retrieving, and updating job state.

    Implementations (Redis-backed, in-memory, ...) are interchangeable behind
    this interface, so both the API and worker can depend on JobStateStore
    without depending on a specific backend.
    """

    async def create(self, state: JobState) -> None:
        """Persist a new job's state."""
        ...

    async def get(self, job_id: str | UUID) -> JobState | None:
        """Return the stored state for job_id, or None if it doesn't exist."""
        ...

    async def update(self, state: JobState) -> None:
        """Replace the stored state for state.job_id.

        Implementations must refuse a non-terminal write over a state that
        already reached a terminal status, so a stale worker cannot reopen
        a finished job.
        """
        ...
