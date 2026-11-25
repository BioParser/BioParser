from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env"),
        env_prefix="BIOPARSER_",
        extra="ignore",
        hide_input_in_errors=True,
    )

    vllm_base_url: str = "http://localhost:8000/v1"
    vllm_api_key: SecretStr = SecretStr("EMPTY")

    vllm_model_discovery_timeout_seconds: float = Field(default=10.0, gt=0)

    vllm_request_timeout_seconds: float = Field(
        default=300.0,
        gt=0,
    )

    # Reserved completion tokens. --max-model-len is the prompt and this reservation
    # together. The prompt is packed to the reported window minus this ceiling.
    # A caller may ask for fewer tokens. A larger request is capped here.
    vllm_max_tokens: int = Field(default=1024, ge=1)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
