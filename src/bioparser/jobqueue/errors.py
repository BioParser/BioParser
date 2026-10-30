__all__ = [
    "JobDeferralExpired",
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


class JobDeferralExpired(Exception):
    """A deferred job was still not runnable when its deadline passed.

    The queue does not send it again. `on_failed` records `failed` only when
    the job's status allows that transition.
    """


class JobDeferred(Exception):
    """The handler cannot run the job yet and has not changed it.

    The queue acknowledges this delivery and sends a new message later. Each
    send waits longer, up to a cap, until a deadline. The wait does not use
    one of the message's retries. Past the deadline the queue stops sending
    and reports `JobDeferralExpired` through `on_failed`.
    """


class JobTimeLimitExceeded(Exception):
    """The job ran past the queue's time limit.

    The handler is cancelled. See `JobHandler.handle`. `on_failed` receives this
    when the backstop gives up on a call that is still running, and on the last
    retry of an attempt that already raised it.
    """
