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


class JobStateBackendError(JobStateError):
    """Redis answered with an error other than a connectivity failure (OOM, WRONGTYPE, NOPERM, READONLY, ...)."""


class TerminalJobStateError(JobStateError):
    """Stored state is already terminal and the update would undo that."""
