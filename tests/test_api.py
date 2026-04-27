from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from lecturelog.config import Settings
from lecturelog.models import PipelineStage, PipelineStatus
from server.app import create_app


def _test_settings(tmp_path: Path | None = None) -> Settings:
    """Настройки с фиктивными ключами для тестов."""
    return Settings(
        GROQ_API_KEY="test-key",
        GEMINI_API_KEYS="test-key-1",
        UPLOAD_DIR=str(tmp_path) if tmp_path else "/tmp/lecturelog-test",
    )


def test_health_endpoint_returns_ok():
    app = create_app(settings=_test_settings())
    client = TestClient(app)

    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_task_lifecycle_endpoints(tmp_path, monkeypatch):
    done_zip = tmp_path / "result.zip"
    done_zip.write_bytes(b"zip")

    async def fake_create_task(self, audio, slides):
        task_id = "task-123"
        self.statuses[task_id] = PipelineStatus(
            task_id=task_id,
            stage=PipelineStage.EXPORT,
            progress_pct=100,
            error=None,
            result_path=str(done_zip),
        )
        return task_id

    monkeypatch.setattr("server.task_manager.TaskManager.create_task", fake_create_task)

    app = create_app(settings=_test_settings(tmp_path))
    client = TestClient(app)

    create_resp = client.post(
        "/api/v1/tasks",
        files={"audio": ("lecture.mp3", b"audio", "audio/mpeg")},
    )
    assert create_resp.status_code == 200
    task_id = create_resp.json()["task_id"]

    status_resp = client.get(f"/api/v1/tasks/{task_id}")
    assert status_resp.status_code == 200
    assert status_resp.json()["progress_pct"] == 100

    result_resp = client.get(f"/api/v1/tasks/{task_id}/result")
    assert result_resp.status_code == 200
    assert result_resp.content == b"zip"


def test_result_not_ready_returns_404(tmp_path, monkeypatch):
    async def fake_create_task(self, audio, slides):
        task_id = "task-404"
        self.statuses[task_id] = PipelineStatus(
            task_id=task_id,
            stage=PipelineStage.TRANSCRIBE,
            progress_pct=30,
            error=None,
            result_path=None,
        )
        return task_id

    monkeypatch.setattr("server.task_manager.TaskManager.create_task", fake_create_task)

    app = create_app(settings=_test_settings(tmp_path))
    client = TestClient(app)

    create_resp = client.post(
        "/api/v1/tasks",
        files={"audio": ("lecture.mp3", b"audio", "audio/mpeg")},
    )
    task_id = create_resp.json()["task_id"]

    result_resp = client.get(f"/api/v1/tasks/{task_id}/result")
    assert result_resp.status_code == 404
