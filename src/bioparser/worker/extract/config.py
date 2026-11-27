import logging
from pathlib import Path

from pydantic import AnyUrl, Field, PostgresDsn, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from bioparser.docker_secrets import SECRETS_DIR
from bioparser.logging_config import parse_level

DEFAULT_EXTRACT_TIME_LIMIT_SECONDS = 10 * 60
DEFAULT_EXTRACT_MAX_RETRIES = 3

# Probe port for GET /health.
DEFAULT_LIVENESS_PORT = 8081


class ExtractWorkerSettings(BaseSettings):
    """Environment configuration for the extractor worker.

    One job runs at a time on the queue's consumer event loop.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="BIOPARSER_",
        extra="ignore",
        hide_input_in_errors=True,
        secrets_dir=SECRETS_DIR,
    )

    log_level: int = logging.INFO
    # A URL can hold its password: repr=False and exclude=True hide it. Compose gives the
    # passwords as Docker secrets instead (see bioparser.docker_secrets).
    redis_url: AnyUrl = Field(repr=False, exclude=True)
    redis_password: SecretStr | None = None
    database_url: PostgresDsn = Field(repr=False, exclude=True)
    database_password: SecretStr | None = None
    artifact_storage_path: Path
    extract_queue_name: str = Field(min_length=1)
    redis_timeout_seconds: float = Field(default=5.0, gt=0)
    extract_time_limit_seconds: float = Field(default=DEFAULT_EXTRACT_TIME_LIMIT_SECONDS, gt=0)
    extract_max_retries: int = Field(default=DEFAULT_EXTRACT_MAX_RETRIES, ge=0)
    liveness_port: int = Field(default=DEFAULT_LIVENESS_PORT, ge=1, le=65535)

    @property
    def extract_max_claims(self) -> int:
        """Extract-stage starts allowed, one more than the queue retry count."""
        return self.extract_max_retries + 1

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

    @field_validator("database_url", mode="before")
    @classmethod
    def empty_database_url_is_rejected(cls, value: object) -> object:
        if value == "":
            raise ValueError("BIOPARSER_DATABASE_URL is set but empty")
        return value
