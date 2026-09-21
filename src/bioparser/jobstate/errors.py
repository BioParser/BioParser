class JobStateError(RuntimeError):
    """Base class for job-state adapter errors."""


class JobNotFoundError(JobStateError):
    """No state exists for the given job ID."""


class JobAlreadyExistsError(JobStateError):
    """A job with this ID already has state."""


class CorruptJobStateError(JobStateError):
    """Stored state could not be parsed into a valid JobState."""


class JobStateConnectionError(JobStateError):
    """Redis was unreachable, refused the connection, or timed out."""
