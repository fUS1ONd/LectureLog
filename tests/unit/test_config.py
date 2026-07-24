import pytest

from lecturelog.config.settings import AppConfig


@pytest.fixture(autouse=True)
def _ignore_project_env_file(monkeypatch, tmp_path):
    # BaseSettings читает .env из текущего каталога; в тестах конфиг задаётся
    # только через monkeypatch, чтобы реальные секреты/локальные настройки не влияли.
    monkeypatch.chdir(tmp_path)


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
    assert cfg.transcribe.groq_keys == ["g1", "g2"]


def test_deepgram_provider_needs_only_deepgram_key(monkeypatch):
    for k, v in _env(
        TRANSCRIBE_PROVIDER="deepgram",
        GROQ_API_KEYS="",
        DEEPGRAM_API_KEY="dg-secret",
    ).items():
        monkeypatch.setenv(k, v)
    cfg = AppConfig()
    assert cfg.transcribe.provider == "deepgram"
    assert cfg.transcribe.deepgram_model == "nova-3"
    assert cfg.transcribe.deepgram_detect_language is False
    assert cfg.transcribe.deepgram_api_key.get_secret_value() == "dg-secret"
    assert "dg-secret" not in repr(cfg.transcribe)


def test_deepgram_language_detection_reads_boolean(monkeypatch):
    for k, v in _env(
        TRANSCRIBE_PROVIDER="deepgram",
        DEEPGRAM_API_KEY="dg-secret",
        DEEPGRAM_DETECT_LANGUAGE="true",
    ).items():
        monkeypatch.setenv(k, v)
    assert AppConfig().transcribe.deepgram_detect_language is True


def test_deepgram_provider_rejects_empty_key(monkeypatch):
    for k, v in _env(
        TRANSCRIBE_PROVIDER="deepgram",
        GROQ_API_KEYS="",
        DEEPGRAM_API_KEY="",
    ).items():
        monkeypatch.setenv(k, v)
    with pytest.raises(Exception, match="DEEPGRAM_API_KEY"):  # noqa: B017
        AppConfig()


@pytest.mark.parametrize(
    "url",
    [
        "http://api.deepgram.com",
        "https://evil.example",
        "https://user@api.deepgram.com",
        "https://api.deepgram.com?x=1",
    ],
)
def test_deepgram_base_url_rejects_unsafe_endpoints(monkeypatch, url):
    for k, v in _env(
        TRANSCRIBE_PROVIDER="deepgram",
        DEEPGRAM_API_KEY="dg-secret",
        DEEPGRAM_BASE_URL=url,
    ).items():
        monkeypatch.setenv(k, v)
    with pytest.raises(Exception, match="DEEPGRAM_BASE_URL"):  # noqa: B017
        AppConfig()


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
    monkeypatch.delenv("LLM_MODELS_FRAMES_CLASSIFY", raising=False)
    monkeypatch.delenv("LLM_EFFORT_FRAMES_CLASSIFY", raising=False)
    for k, v in _env().items():
        monkeypatch.setenv(k, v)
    cfg = AppConfig()
    assert cfg.frames.enabled is True
    assert cfg.frames.models[0] == "google/gemini-3.1-flash-lite"
    # Классификация режимов — на тяжёлой модели с бóльшим effort (1-2 вызова);
    # high: её решения самые нагруженные, а на medium модель недетерминированно
    # путала слайды с доской (ч/б board-рендер на слайдовых лекциях)
    assert cfg.frames.classify_models[0] == "google/gemini-3.5-flash"
    assert cfg.frames.classify_effort == "high"


def test_effort_defaults_medium_for_content_stages(monkeypatch):
    # Стадии, отвечающие за качество текста конспекта, на low эпизодически
    # игнорируют инструкции промпта (реальный кейс — английские заголовки и
    # секции вопреки требованию русского) → дефолт medium
    for k, v in _env().items():
        monkeypatch.setenv(k, v)
    for key in (
        "LLM_EFFORT_SPLIT",
        "LLM_EFFORT_SUBSPLIT",
        "LLM_EFFORT_RENDER",
        "LLM_EFFORT_VIDEO_SLIDES",
    ):
        monkeypatch.delenv(key, raising=False)
    cfg = AppConfig()
    assert cfg.llm.effort_split == "medium"
    assert cfg.llm.effort_subsplit == "medium"
    assert cfg.llm.effort_render == "medium"
    assert cfg.frames.effort == "medium"


def test_frames_models_split_into_list(monkeypatch):
    for k, v in _env(LLM_MODELS_VIDEO_SLIDES="a, b ,c").items():
        monkeypatch.setenv(k, v)
    cfg = AppConfig()
    assert cfg.frames.models == ["a", "b", "c"]


def test_frames_classify_models_split_into_list(monkeypatch):
    for k, v in _env(
        LLM_MODELS_FRAMES_CLASSIFY="classify-a, classify-b ,classify-c",
        LLM_EFFORT_FRAMES_CLASSIFY="high",
    ).items():
        monkeypatch.setenv(k, v)
    cfg = AppConfig()
    assert cfg.frames.classify_models == ["classify-a", "classify-b", "classify-c"]
    assert cfg.frames.classify_effort == "high"


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
