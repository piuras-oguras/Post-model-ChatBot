from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class GuardModelSettings(BaseSettings):
    """Connection settings for the guard-model LLM (OpenAI-compatible endpoint)."""

    base_url: str = "http://localhost:11434/v1"
    api_key: str = "not-needed"
    name: str = "gpt-oss-safeguard:20b"
    request_timeout_seconds: float = 180.0

class OutputGuardSettings(BaseSettings):
    """Default behavior for the output guard when a request omits requires_grounding."""

    requires_grounding: bool = False

class Settings(BaseSettings):
    # Env vars are nested with "__" and prefixed, e.g. POSTMODEL__GUARD_MODEL__NAME.
    model_config = SettingsConfigDict(
        env_file=".env",
        env_nested_delimiter="__",
        env_prefix="POSTMODEL__",
        extra="ignore",
    )

    host: str = "0.0.0.0"
    port: int = 8011

    # Bearer token for /v1/check; empty disables auth (see require_api_token).
    api_token: str = ""

    guard_model: GuardModelSettings = Field(default_factory=GuardModelSettings)
    output_guard: OutputGuardSettings = Field(default_factory=OutputGuardSettings)


@lru_cache
def get_settings() -> Settings:
    """Cached so settings are parsed from env once per process."""
    return Settings()