import logging
from functools import lru_cache

from pydantic import AnyUrl, Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from bioparser.logging_config import parse_level

DEFAULT_MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MiB


class ApiSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="BIOPARSER_",
        extra="ignore",
        hide_input_in_errors=True,
    )
    # Using the numeric level. BIOPARSER_LOG_LEVEL takes a name in any case or a number:
    # see logging_config.parse_level().
    log_level: int = logging.INFO

    max_upload_bytes: int = Field(default=DEFAULT_MAX_UPLOAD_BYTES, ge=1)
    mineru_base_url: str = "http://mineru:8000"
    mineru_timeout_seconds: float = Field(default=600.0, gt=0)
    mineru_connect_timeout_seconds: float = Field(default=5.0, gt=0)
    extract_concurrency: int = Field(default=2, ge=1)
    extract_queue_timeout_seconds: float = Field(default=5.0, gt=0)
    dependency_check_timeout_seconds: float = Field(default=1.0, gt=0)
    artifact_storage_path: str | None = None
    # NOTE: char budget not token budget
    # --max-model-len is cap for all tokens
    extraction_char_budget: int = Field(default=8000, ge=50)
    extraction_max_tokens: int = Field(default=1024, ge=64)

    # redis
    # repr=False: the URL can hold the password; keep it out of repr(settings)
    redis_url: AnyUrl | None = Field(default=None, repr=False)
    redis_timeout_seconds: float = Field(default=5.0, gt=0)

    @field_validator("log_level", mode="before")
    @classmethod
    def parse_log_level(cls, value: object) -> int:
        return parse_level(value)

    @field_validator("redis_url", mode="before")
    @classmethod
    def empty_redis_url_becomes_none(cls, value: object) -> object:
        if value == "":
            return None
        return value


@lru_cache(maxsize=1)
def get_api_settings() -> ApiSettings:
    return ApiSettings()


def invalid_settings(exc: ValidationError) -> list[dict[str, str]]:
    """Settings that failed validation, with pydantic's error type, for logs.
    Input and message are redacted.
    """
    return [
        {"setting": ".".join(map(str, error["loc"])), "type": error["type"]}
        for error in exc.errors(include_url=False, include_context=False, include_input=False)
    ]
