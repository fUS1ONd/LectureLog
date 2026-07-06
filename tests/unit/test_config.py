import pytest

from lecturelog.config.settings import AppConfig


def _env(**overrides):
    base = {
        "GROQ_API_KEYS": "g1,g2",
        "OPENROUTER_API_KEY": "or-k1",
        "DATABASE_URL": "postgresql+asyncpg://u:p@db:5432/lecturelog",
        # S3-поля обязательны для построения AppConfig (см. S3Config).
        "S3_INTERNAL_ENDPOINT": "http://minio:9000",
        "S3_BUCKET": "lectures",
        "S3_ACCESS_KEY": "ak",
        "S3_SECRET_KEY": "sk",
    }
    base.update(overrides)
    return base


def test_groq_keys_parsed_and_trimmed(monkeypatch):
    for k, v in _env().items():
        monkeypatch.setenv(k, v)
    cfg = AppConfig()
    assert cfg.groq.keys == ["g1", "g2"]


def test_llm_models_split_into_lists(monkeypatch):
    for k, v in _env(LLM_MODELS_RENDER="a, b ,c").items():
        monkeypatch.setenv(k, v)
    cfg = AppConfig()
    assert cfg.llm.render_models == ["a", "b", "c"]


def test_worker_default_concurrency(monkeypatch):
    for k, v in _env().items():
        monkeypatch.setenv(k, v)
    cfg = AppConfig()
    assert cfg.worker.max_concurrent_tasks == 2


def test_missing_required_key_raises(monkeypatch):
    # Изолируем именно обязательность OPENROUTER_API_KEY: остальное окружение полное.
    for k, v in _env().items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(Exception, match="OPENROUTER_API_KEY"):  # noqa: B017
        AppConfig()


def test_webhook_config_defaults_none(monkeypatch):
    # Без env-переменных оба поля вебхука опциональны и равны None (автономный режим).
    monkeypatch.delenv("PLATFORM_CALLBACK_URL", raising=False)
    monkeypatch.delenv("LECTURELOG_WEBHOOK_SECRET", raising=False)
    for k, v in _env().items():
        monkeypatch.setenv(k, v)
    cfg = AppConfig()
    assert cfg.webhook.callback_url is None
    assert cfg.webhook.secret is None


def test_webhook_config_reads_env(monkeypatch):
    # Заданные URL и секрет читаются из окружения.
    for k, v in _env().items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("PLATFORM_CALLBACK_URL", "https://p/cb")
    monkeypatch.setenv("LECTURELOG_WEBHOOK_SECRET", "s3cr3t")
    cfg = AppConfig()
    assert cfg.webhook.callback_url == "https://p/cb"
    assert cfg.webhook.secret == "s3cr3t"


def test_s3_config_reads_env(monkeypatch):
    # S3-поля обязательны; public опционален (по умолчанию None).
    monkeypatch.delenv("S3_PUBLIC_ENDPOINT", raising=False)
    for k, v in _env(
        S3_INTERNAL_ENDPOINT="http://minio:9000",
        S3_BUCKET="lectures",
        S3_ACCESS_KEY="ak",
        S3_SECRET_KEY="sk",
    ).items():
        monkeypatch.setenv(k, v)
    cfg = AppConfig()
    assert cfg.s3.internal_endpoint == "http://minio:9000"
    assert cfg.s3.bucket == "lectures"
    assert cfg.s3.public_endpoint is None  # опционален


def test_frames_config_defaults(monkeypatch):
    # По умолчанию стадия кадров включена, модели VLM — fallback-список с flash-lite первым.
    monkeypatch.delenv("FRAMES_ENABLED", raising=False)
    monkeypatch.delenv("LLM_MODELS_VIDEO_SLIDES", raising=False)
    for k, v in _env().items():
        monkeypatch.setenv(k, v)
    cfg = AppConfig()
    assert cfg.frames.enabled is True
    assert cfg.frames.models[0] == "google/gemini-3.1-flash-lite"


def test_frames_models_split_into_list(monkeypatch):
    for k, v in _env(LLM_MODELS_VIDEO_SLIDES="a, b ,c").items():
        monkeypatch.setenv(k, v)
    cfg = AppConfig()
    assert cfg.frames.models == ["a", "b", "c"]


def test_s3_public_endpoint_optional(monkeypatch):
    # При заданном S3_PUBLIC_ENDPOINT он читается (presigned наружу включается).
    for k, v in _env(
        S3_INTERNAL_ENDPOINT="http://minio:9000",
        S3_BUCKET="lectures",
        S3_ACCESS_KEY="ak",
        S3_SECRET_KEY="sk",
        S3_PUBLIC_ENDPOINT="https://files.example",
    ).items():
        monkeypatch.setenv(k, v)
    cfg = AppConfig()
    assert cfg.s3.public_endpoint == "https://files.example"
