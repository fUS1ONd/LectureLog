from __future__ import annotations

from functools import cached_property, lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, computed_field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_BASE = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


def _split_csv(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


class TranscribeConfig(BaseSettings):
    model_config = _BASE
    provider: Literal["groq", "deepgram"] = Field("groq", alias="TRANSCRIBE_PROVIDER")
    groq_api_keys_raw: str = Field("", alias="GROQ_API_KEYS")
    deepgram_api_key: SecretStr | None = Field(None, alias="DEEPGRAM_API_KEY")
    deepgram_base_url: str = Field("https://api.deepgram.com", alias="DEEPGRAM_BASE_URL")
    deepgram_model: str = Field("nova-3", alias="DEEPGRAM_MODEL")
    deepgram_language: str = Field("ru", alias="DEEPGRAM_LANGUAGE")
    deepgram_detect_language: bool = Field(False, alias="DEEPGRAM_DETECT_LANGUAGE")
    deepgram_utt_split: float = Field(0.8, alias="DEEPGRAM_UTT_SPLIT", gt=0)

    @model_validator(mode="after")
    def validate_provider(self) -> TranscribeConfig:
        parts = urlsplit(self.deepgram_base_url)
        allowed_hosts = {
            "api.deepgram.com",
            "api.eu.deepgram.com",
            "api.au.deepgram.com",
        }
        if (
            parts.scheme != "https"
            or parts.hostname not in allowed_hosts
            or parts.username is not None
            or parts.password is not None
            or parts.query
            or parts.fragment
            or parts.path not in ("", "/")
        ):
            raise ValueError("DEEPGRAM_BASE_URL должен быть официальным HTTPS endpoint Deepgram")
        self.deepgram_base_url = self.deepgram_base_url.rstrip("/")
        if self.provider == "groq" and not self.groq_keys:
            raise ValueError("GROQ_API_KEYS обязателен при TRANSCRIBE_PROVIDER=groq")
        if self.provider == "deepgram" and (
            self.deepgram_api_key is None or not self.deepgram_api_key.get_secret_value().strip()
        ):
            raise ValueError("DEEPGRAM_API_KEY обязателен при TRANSCRIBE_PROVIDER=deepgram")
        return self

    @property
    def groq_keys(self) -> list[str]:
        return _split_csv(self.groq_api_keys_raw)


class LlmConfig(BaseSettings):
    # Транспорт LLM переведён на OpenRouter (BYOK): один ключ и base_url
    # вместо пула ключей Gemini. Модели указываются с префиксом провайдера
    # (например, "google/gemini-3.6-flash").
    model_config = _BASE
    openrouter_key: str = Field(alias="OPENROUTER_API_KEY")
    base_url: str = Field("https://openrouter.ai/api/v1", alias="OPENROUTER_BASE_URL")

    models_split: str = Field(
        "google/gemini-3.6-flash,google/gemini-3.5-flash,google/gemini-3.5-flash-lite",
        alias="LLM_MODELS_SPLIT",
    )
    models_subsplit: str = Field(
        "google/gemini-3.6-flash,google/gemini-3.5-flash,google/gemini-3.5-flash-lite",
        alias="LLM_MODELS_SUBSPLIT",
    )
    models_render: str = Field(
        "google/gemini-3.5-flash-lite,google/gemini-3.5-flash,google/gemini-3.6-flash",
        alias="LLM_MODELS_RENDER",
    )
    # Потолок ответа (включая reasoning-токены). Обрезанный JSON рвал каталог
    # слайдов молча, поэтому по умолчанию берём предел самих моделей Gemini.
    max_tokens: int = Field(65536, alias="LLM_MAX_TOKENS")
    concurrency_subsplit: int = Field(2, alias="LLM_CONCURRENCY_SUBSPLIT")
    concurrency_render: int = Field(5, alias="LLM_CONCURRENCY_RENDER")
    # reasoning effort по стадиям: контентные стадии — medium; на low модель
    # эпизодически игнорирует инструкции промпта (реальный кейс — английские
    # заголовки/секции вопреки требованию русского)
    effort_split: str = Field("medium", alias="LLM_EFFORT_SPLIT")
    effort_subsplit: str = Field("medium", alias="LLM_EFFORT_SUBSPLIT")
    effort_render: str = Field("medium", alias="LLM_EFFORT_RENDER")
    # Сопоставление слайдов идёт строго структурированными ответами: с ростом
    # reasoning модель хуже держит схему (пропускает обязательные поля), поэтому
    # у матчера свой effort, независимый от контентных стадий.
    effort_slide_match: str = Field("low", alias="LLM_EFFORT_SLIDE_MATCH")
    # Своя ротация моделей у матчера: Gemini обрывает каталог слайдов по
    # RECITATION на страницах с цитатами из официальных документов, а открытые
    # веса (gemma) тем же фильтром не ограничены. Пусто — берём модели SUBSPLIT,
    # то есть прежнее поведение.
    models_slide_match: str = Field("", alias="LLM_MODELS_SLIDE_MATCH")

    @property
    def split_models(self) -> list[str]:
        return _split_csv(self.models_split)

    @property
    def subsplit_models(self) -> list[str]:
        return _split_csv(self.models_subsplit)

    @property
    def render_models(self) -> list[str]:
        return _split_csv(self.models_render)

    @property
    def slide_match_models(self) -> list[str]:
        return _split_csv(self.models_slide_match) or self.subsplit_models


class FramesConfig(BaseSettings):
    # Стадия извлечения кадров из видео (дизайн 2026-07-05-video-frames-design.md).
    # Модели VLM — обычный fallback-список через ModelCooldown; flash-lite первым.
    model_config = _BASE
    enabled: bool = Field(True, alias="FRAMES_ENABLED")
    models_raw: str = Field(
        "google/gemini-3.5-flash-lite,google/gemini-3.6-flash,google/gemini-3.5-flash",
        alias="LLM_MODELS_VIDEO_SLIDES",
    )
    effort: str = Field("medium", alias="LLM_EFFORT_VIDEO_SLIDES")
    # Классификация режимов — 1-2 вызова на лекцию, но её решения (тип, bbox,
    # board_kind) самые нагруженные: тяжёлая модель + high почти бесплатны.
    # На medium модель недетерминированно путала слайды с доской (реальный
    # кейс: ч/б board-рендер слайдовых сегментов). QC — на дешёвом списке выше.
    classify_models_raw: str = Field(
        "google/gemini-3.6-flash,google/gemini-3.5-flash,google/gemini-3.5-flash-lite",
        alias="LLM_MODELS_FRAMES_CLASSIFY",
    )
    classify_effort: str = Field("high", alias="LLM_EFFORT_FRAMES_CLASSIFY")

    @property
    def models(self) -> list[str]:
        return _split_csv(self.models_raw)

    @property
    def classify_models(self) -> list[str]:
        return _split_csv(self.classify_models_raw)


class DocumentSlidesConfig(BaseSettings):
    model_config = _BASE
    alignment_mode: Literal["legacy", "shadow", "v2"] = Field(
        "legacy", alias="DOCUMENT_SLIDE_ALIGNMENT_MODE"
    )
    candidate_limit: int = Field(5, ge=1, le=12, alias="DOCUMENT_SLIDE_CANDIDATE_LIMIT")
    neighbor_radius: int = Field(1, ge=0, le=3, alias="DOCUMENT_SLIDE_NEIGHBOR_RADIUS")
    deck_min_supported_ratio: float = Field(
        0.08, ge=0.0, le=1.0, alias="DOCUMENT_SLIDE_DECK_MIN_SUPPORTED_RATIO"
    )


class DatabaseConfig(BaseSettings):
    model_config = _BASE
    url: str = Field(alias="DATABASE_URL")


class S3Config(BaseSettings):
    # Два endpoint'а на один MinIO: internal — движок внутри docker-сети;
    # public (опц.) — хост для presigned в браузер. Без public presigned наружу не выдаётся.
    model_config = _BASE
    internal_endpoint: str = Field(alias="S3_INTERNAL_ENDPOINT")
    public_endpoint: str | None = Field(None, alias="S3_PUBLIC_ENDPOINT")
    bucket: str = Field(alias="S3_BUCKET")
    access_key: str = Field(alias="S3_ACCESS_KEY")
    secret_key: str = Field(alias="S3_SECRET_KEY")
    region: str = Field("us-east-1", alias="S3_REGION")
    presign_expiry: int = Field(3600, alias="S3_PRESIGN_EXPIRY")


class WorkerConfig(BaseSettings):
    model_config = _BASE
    max_concurrent_tasks: int = Field(2, alias="MAX_CONCURRENT_TASKS")


class MediaConfig(BaseSettings):
    model_config = _BASE
    target_resolution: str = Field("720", alias="VIDEO_TARGET_RESOLUTION")

    @field_validator("target_resolution")
    @classmethod
    def validate_target_resolution(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized == "best":
            return normalized
        try:
            resolution = int(normalized)
        except ValueError as exc:
            raise ValueError(
                "VIDEO_TARGET_RESOLUTION должен быть best или числом 144..4320"
            ) from exc
        if not 144 <= resolution <= 4320:
            raise ValueError("VIDEO_TARGET_RESOLUTION должен быть в диапазоне 144..4320")
        return str(resolution)


class WebhookConfig(BaseSettings):
    # Оба поля опциональны: режим вебхука включается только при заданном callback_url.
    # Без URL движок работает автономно (поллинг-эндпоинты), поведение не меняется.
    model_config = _BASE
    callback_url: str | None = Field(None, alias="PLATFORM_CALLBACK_URL")
    secret: str | None = Field(None, alias="LECTURELOG_WEBHOOK_SECRET")


class AppConfig(BaseSettings):
    # Сборка под-конфигов как computed-полей: каждый блок сам читает env,
    # поэтому AppConfig не объявляет собственных env-полей и ничего не валидирует напрямую.
    model_config = _BASE

    def model_post_init(self, __context: object) -> None:
        # Форсируем создание под-конфигов сразу, чтобы ключ выбранного
        # STT-провайдера и остальные required-поля проверялись при построении AppConfig.
        _ = (
            self.transcribe,
            self.llm,
            self.database,
            self.s3,
            self.worker,
            self.media,
            self.webhook,
            self.frames,
            self.document_slides,
        )

    @computed_field  # type: ignore[prop-decorator]
    @cached_property
    def transcribe(self) -> TranscribeConfig:
        return TranscribeConfig()

    @computed_field  # type: ignore[prop-decorator]
    @cached_property
    def llm(self) -> LlmConfig:
        return LlmConfig()

    @computed_field  # type: ignore[prop-decorator]
    @cached_property
    def database(self) -> DatabaseConfig:
        return DatabaseConfig()

    @computed_field  # type: ignore[prop-decorator]
    @cached_property
    def s3(self) -> S3Config:
        return S3Config()

    @computed_field  # type: ignore[prop-decorator]
    @cached_property
    def worker(self) -> WorkerConfig:
        return WorkerConfig()

    @computed_field  # type: ignore[prop-decorator]
    @cached_property
    def media(self) -> MediaConfig:
        return MediaConfig()

    @computed_field  # type: ignore[prop-decorator]
    @cached_property
    def webhook(self) -> WebhookConfig:
        return WebhookConfig()

    @computed_field  # type: ignore[prop-decorator]
    @cached_property
    def frames(self) -> FramesConfig:
        return FramesConfig()

    @computed_field  # type: ignore[prop-decorator]
    @cached_property
    def document_slides(self) -> DocumentSlidesConfig:
        return DocumentSlidesConfig()


@lru_cache
def get_config() -> AppConfig:
    return AppConfig()
