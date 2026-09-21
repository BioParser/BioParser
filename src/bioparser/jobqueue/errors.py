class JobQueueError(Exception):
    """Failures in job-queue transport."""


class JobQueueConfigError(JobQueueError):
    """Invalid queue configuration."""
