from __future__ import annotations

import os
from functools import lru_cache

try:
    from pydantic_settings import BaseSettings, SettingsConfigDict
except Exception:  # pragma: no cover
    from pydantic import BaseModel

    class BaseSettings(BaseModel):  # type: ignore
        def __init__(self, **data):
            values = {}
            for key in self.model_fields:
                if key in os.environ:
                    values[key] = os.environ[key]
            values.update(data)
            super().__init__(**values)

    SettingsConfigDict = dict  # type: ignore


class Settings(BaseSettings):
    GROQ_API_KEY: str = ""
    GEMINI_API_KEYS: str = ""
    GEMINI_MODEL: str = "gemini-2.5-pro"

    UPLOAD_DIR: str = "/tmp/lecturelog"
    MAX_WORKERS: int = 5

    TELEGRAM_BOT_TOKEN: str = ""
    API_BASE_URL: str = "http://localhost:8000"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def gemini_api_keys(self) -> list[str]:
        return [key.strip() for key in self.GEMINI_API_KEYS.split(",") if key.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
