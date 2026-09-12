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
    vllm_api_key: str = "EMPTY"

    model_discovery_timeout_seconds: float = Field(default=10.0, gt=0)

    request_timeout_seconds: float = Field(
        default=300.0,
        gt=0,
    )

    max_concurrency: int = Field(
        default=4,
        ge=1,
    )

    # --max-model-len is server-side cap e.g. 4096
    # from that we use tokens to system_prompt ~100
    #               CHAR_BUDGET (extract.py)  ~2400 ?
    #               and client side cap 1024  ~1024
    #
    # So if this is raised, --max-model-len should be changed in docker-compose
    max_tokens: int = Field(default=1024, ge=1)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
