from __future__ import annotations

from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    GROQ_API_KEYS: str  # один ключ или несколько через запятую
    GEMINI_API_KEYS: str
    GEMINI_MODEL: str = "gemini-2.5-pro"
    # приоритетный список моделей Gemini через запятую
    GEMINI_MODELS: str = "gemini-3-flash-preview,gemini-2.5-flash,gemini-2.5-flash-lite"
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
    def groq_api_keys(self) -> List[str]:
        return [k.strip() for k in self.GROQ_API_KEYS.split(",") if k.strip()]

    @property
    def gemini_api_keys(self) -> List[str]:
        return [item.strip() for item in self.GEMINI_API_KEYS.split(",") if item.strip()]

    @property
    def gemini_models(self) -> List[str]:
        # список моделей в порядке приоритета
        return [m.strip() for m in self.GEMINI_MODELS.split(",") if m.strip()]


@lru_cache
def get_settings() -> Settings:
    """Кэшированный синглтон настроек."""
    return Settings()
