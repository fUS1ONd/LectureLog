import pytest

pytest.importorskip("pydantic_settings")

from lecturelog.config import Settings
from lecturelog.models import PipelineStage, PipelineStatus, Section


def test_settings_defaults_and_parsing() -> None:
    # Передаём все обязательные поля явно, чтобы не зависеть от .env
    settings = Settings(
        GROQ_API_KEYS="groq-key1,groq-key2",
        GEMINI_API_KEYS="k1, k2 ,k3",
        UPLOAD_DIR="/app/data",
    )

    assert settings.GEMINI_MODELS == "gemini-3-flash-preview,gemini-2.5-flash,gemini-2.5-flash-lite"
    assert settings.gemini_models == ["gemini-3-flash-preview", "gemini-2.5-flash", "gemini-2.5-flash-lite"]
    assert settings.UPLOAD_DIR == "/app/data"
    assert settings.MAX_WORKERS == 5
    assert settings.API_BASE_URL == "http://localhost:8000"
    assert settings.groq_api_keys == ["groq-key1", "groq-key2"]
    assert settings.gemini_api_keys == ["k1", "k2", "k3"]


def test_pipeline_status_model() -> None:
    status = PipelineStatus(
        task_id="task-1",
        stage=PipelineStage.TRANSCRIBE,
        progress_pct=40,
    )

    assert status.error is None
    assert status.result_path is None


def test_section_model() -> None:
    section = Section(
        title="Введение",
        start="00:00:00",
        end="00:01:00",
        content="Текст",
        slide_indices=[1, 2],
    )

    assert section.slide_indices == [1, 2]


def test_gemini_models_parsed_from_env(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEYS", "gsk_test")
    monkeypatch.setenv("GEMINI_API_KEYS", "key1")
    monkeypatch.setenv("GEMINI_MODELS", "gemini-3-flash-preview,gemini-2.5-flash,gemini-2.5-flash-lite")
    monkeypatch.setenv("UPLOAD_DIR", "/tmp/test")
    from lecturelog.config import Settings
    s = Settings()
    assert s.gemini_models == ["gemini-3-flash-preview", "gemini-2.5-flash", "gemini-2.5-flash-lite"]

def test_gemini_models_default(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEYS", "gsk_test")
    monkeypatch.setenv("GEMINI_API_KEYS", "key1")
    monkeypatch.setenv("UPLOAD_DIR", "/tmp/test")
    monkeypatch.delenv("GEMINI_MODELS", raising=False)
    from lecturelog.config import Settings
    s = Settings()
    assert s.gemini_models == ["gemini-3-flash-preview", "gemini-2.5-flash", "gemini-2.5-flash-lite"]
