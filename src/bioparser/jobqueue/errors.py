class JobQueueError(Exception):
    """Failures in job-queue transport."""


class JobQueueConfigError(JobQueueError):
    """Invalid queue configuration."""


class MalformedJob(JobQueueError):
    """Queue payload that cannot be validated as the bound message type."""
