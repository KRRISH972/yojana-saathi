"""Application configuration, loaded once from environment variables (or .env locally).

Every other module reads settings from here — nothing else calls os.environ or reads
.env directly, so there is exactly one place that knows where configuration comes from.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration the app needs. Field names match the environment variable names,
    case-insensitively (see .env.example)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    gemini_api_key: str
    gemini_model: str = "gemini-3.8-flash"


@lru_cache
def get_settings() -> Settings:
    """Return the cached Settings instance, built once per process."""
    return Settings()
