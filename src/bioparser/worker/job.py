"""Load one stored job for a worker.

The result says whether the record is present, missing, or unreadable.
The caller decides what that means for the delivery.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from bioparser.jobstate import CorruptJobStateError, JobState, JobStateStore


@dataclass(frozen=True, slots=True)
class MissingJobRecord:
    """No job is stored for this id."""


@dataclass(frozen=True, slots=True)
class CorruptJobRecord:
    """The stored job could not be read."""


async def fetch_job(
    jobs: JobStateStore, job_id: UUID
) -> JobState | MissingJobRecord | CorruptJobRecord:
    """Load one job. The caller decides whether a missing or corrupt record is retried."""
    try:
        state = await jobs.get(job_id)
    except CorruptJobStateError:
        return CorruptJobRecord()
    if state is None:
        return MissingJobRecord()
    return state
