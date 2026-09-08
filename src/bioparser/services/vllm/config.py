from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# TODO: expand config
class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env"),
        env_prefix="BIOPARSER_",
        extra="ignore",
    )

    vllm_base_url: str = "http://localhost:8000/v1"

    request_timeout_seconds: float = Field(
        default=300.0,
        gt=0,
    )

    max_concurrency: int = Field(
        default=4,
        ge=1,
    )

    max_chunk_tokens: int = Field(
        default=4000,
        ge=256,
    )

    chunk_overlap_tokens: int = Field(
        default=300,
        ge=0,
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
