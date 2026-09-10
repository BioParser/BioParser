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


@lru_cache(maxsize=1)
def get_api_settings() -> ApiSettings:
    return ApiSettings()
