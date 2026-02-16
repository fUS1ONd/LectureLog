from lecturelog.config import Settings
from lecturelog.models import PipelineStage, PipelineStatus, Section


def test_settings_defaults_and_parsing() -> None:
    settings = Settings(
        GROQ_API_KEY="groq-key",
        GEMINI_API_KEYS="k1, k2 ,k3",
    )

    assert settings.GEMINI_MODEL == "gemini-2.5-pro"
    assert settings.UPLOAD_DIR == "/tmp/lecturelog"
    assert settings.MAX_WORKERS == 5
    assert settings.API_BASE_URL == "http://localhost:8000"
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

