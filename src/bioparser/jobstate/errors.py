class JobStateError(RuntimeError):
    """A job read or write failed."""


class JobNotFoundError(JobStateError):
    """No state exists for the given job ID."""


class JobAlreadyExistsError(JobStateError):
    """A job with this ID already has state."""


class CorruptJobStateError(JobStateError):
    """Stored state could not be parsed into a valid JobState."""


class StaleJobStateWriteError(JobStateError):
    """The update is not an allowed transition from the stored status (stale or duplicate write)."""
