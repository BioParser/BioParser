class JobStateError(RuntimeError):
    """A pipeline rule rejected a job read or write."""


class JobNotFoundError(JobStateError):
    """No state exists for the given job ID."""


class CorruptJobStateError(JobStateError):
    """Stored state could not be parsed into a valid JobState."""
