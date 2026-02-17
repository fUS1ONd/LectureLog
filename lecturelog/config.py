from __future__ import annotations

from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    GROQ_API_KEY: str
    GEMINI_API_KEYS: str
    GEMINI_MODEL: str = "gemini-2.5-pro"
    UPLOAD_DIR: str = "/app/data"
    MAX_WORKERS: int = 5
    TELEGRAM_BOT_TOKEN: str = ""
    API_BASE_URL: str = "http://localhost:8000"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def gemini_api_keys(self) -> List[str]:
        return [item.strip() for item in self.GEMINI_API_KEYS.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    """Кэшированный синглтон настроек."""
    return Settings()
