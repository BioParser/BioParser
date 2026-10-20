__all__ = [
    "JobDeferred",
    "JobQueueConfigError",
    "JobQueueError",
    "JobTimeLimitExceeded",
    "MalformedJob",
]


class JobQueueError(Exception):
    """Failures in job-queue transport."""


class JobQueueConfigError(JobQueueError):
    """Invalid queue configuration."""


class MalformedJob(JobQueueError):
    """Queue payload that cannot be validated as the bound message type."""


class JobDeferred(Exception):
    """The handler cannot run the job yet and has not changed it.

    The queue acknowledges this delivery and sends the message again later.
    The wait does not use one of the message's retries.
    """


class JobTimeLimitExceeded(BaseException):
    """Raised by the queue in a handler's thread when its time limit passes.

    It is injected into the running thread, so it can surface at any statement. Like
    `KeyboardInterrupt` it is not an `Exception`: a plain `except Exception` does not
    swallow it. A handler may catch it to decide what a timeout means, for example whether
    to retry, and otherwise lets it pass.
    """
