from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MiB


class ApiSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="BIOPARSER_",
        extra="ignore",
    )

    max_upload_bytes: int = Field(default=DEFAULT_MAX_UPLOAD_BYTES, ge=1)
    mineru_base_url: str = "http://mineru:8000"
    mineru_timeout_seconds: float = Field(default=600.0, gt=0)
    mineru_connect_timeout_seconds: float = Field(default=5.0, gt=0)
    extract_concurrency: int = Field(default=2, ge=1)
    extract_queue_timeout_seconds: float = Field(default=5.0, gt=0)
    # NOTE: char budget not token budget
    # --max-model-len is cap for all tokens
    extraction_char_budget: int = Field(default=8000, ge=50)
    extraction_max_tokens: int = Field(default=1024, ge=64)


@lru_cache(maxsize=1)
def get_api_settings() -> ApiSettings:
    return ApiSettings()
