import logging
from pathlib import Path

from pydantic import AnyUrl, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from bioparser.logging_config import parse_level

# MinerU's pipeline backend runs in-process on CPU and can take much longer than
# the queue's 10 minute default. A shorter limit kills the parse and retries it.
DEFAULT_PARSE_TIME_LIMIT_SECONDS = 30 * 60
DEFAULT_PARSE_MAX_RETRIES = 3
# Head start the parser process limit has over the queue limit, so the process is
# stopped and reported before the queue interrupts the thread. Also covers fetching
# the PDF and storing the artifact. Capped at half the limit for small limits.
PARSE_TIME_LIMIT_MARGIN_S = 30.0


class WorkerSettings(BaseSettings):
    """Environment configuration for the parser worker.

    One job runs at a time. The handler drives an asyncio job-state client and
    the MinerU CLI from the queue thread, so the process does not raise the
    queue's worker thread count.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="BIOPARSER_",
        extra="ignore",
        hide_input_in_errors=True,
    )

    # Using the numeric level. BIOPARSER_LOG_LEVEL takes a name in any case or a number:
    # see logging_config.parse_level().
    log_level: int = logging.INFO

    redis_url: AnyUrl
    artifact_storage_path: Path
    parse_queue_name: str = Field(min_length=1)
    redis_timeout_seconds: float = Field(default=5.0, gt=0)
    parse_time_limit_seconds: float = Field(default=DEFAULT_PARSE_TIME_LIMIT_SECONDS, gt=0)
    parse_max_retries: int = Field(default=DEFAULT_PARSE_MAX_RETRIES, ge=0)

    @property
    def parse_max_claims(self) -> int:
        """Parse-stage starts allowed, one more than the queue retry count.

        The extra start lets a full set of ordinary retries finish. Starts where
        the worker was killed share the same budget.
        """
        return self.parse_max_retries + 1

    @property
    def parser_timeout_seconds(self) -> float:
        """Limit for the MinerU process, slightly below the queue's time limit."""
        limit_s = self.parse_time_limit_seconds
        return limit_s - min(PARSE_TIME_LIMIT_MARGIN_S, limit_s / 2)

    @field_validator("log_level", mode="before")
    @classmethod
    def parse_log_level(cls, value: object) -> int:
        return parse_level(value)

    @field_validator("redis_url", mode="before")
    @classmethod
    def empty_redis_url_is_rejected(cls, value: object) -> object:
        if value == "":
            raise ValueError("BIOPARSER_REDIS_URL is set but empty")
        return value
