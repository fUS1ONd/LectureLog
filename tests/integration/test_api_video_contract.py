import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path):
    from lecturelog.api import dependencies as deps
    from lecturelog.api.app import create_app

    app = create_app()

    class InMemoryRepo:
        def __init__(self):
            self.tasks = {}

        async def create(self, t):
            self.tasks[t.task_id] = t

        async def get(self, tid):
            return self.tasks.get(tid)

        async def update(self, t):
            self.tasks[t.task_id] = t

        async def mark_stale_as_interrupted(self):
            return 0

    class RecordingWorker:
        def __init__(self):
            self.jobs = []

        async def enqueue(self, job):
            self.jobs.append(job)

    from tests.support.fake_storage import FakeStorage

    repo = InMemoryRepo()
    worker = RecordingWorker()
    storage = FakeStorage(public=False)
    # Без контекст-менеджера TestClient: не запускаем реальный lifespan
    # (Postgres/Gemini), проверяем только HTTP-контракт через overrides + app.state.
    app.dependency_overrides[deps.get_repository] = lambda: repo
    app.dependency_overrides[deps.get_worker] = lambda: worker
    app.dependency_overrides[deps.get_work_dir] = lambda: tmp_path
    app.dependency_overrides[deps.get_storage] = lambda: storage
    app.state.repository = repo
    app.state.worker = worker
    app.state.work_dir = tmp_path
    app.state.storage = storage
    app.state.llm = object()
    app.state.prompts_dir = tmp_path
    c = TestClient(app)
    c._repo = repo
    c._worker = worker
    yield c


def test_video_file_returns_task_id(client):
    r = client.post("/api/v1/tasks", files={"video": ("v.mp4", b"data", "video/mp4")})
    assert r.status_code == 200
    assert "task_id" in r.json()
    job = client._worker.jobs[-1]
    assert job.source.kind == "video_file"


def test_video_url_returns_task_id(client):
    r = client.post("/api/v1/tasks", data={"video_url": "https://youtu.be/abc"})
    assert r.status_code == 200
    job = client._worker.jobs[-1]
    assert job.source.kind == "video_url"
    assert job.source.url == "https://youtu.be/abc"


def test_two_sources_still_400(client):
    r = client.post(
        "/api/v1/tasks",
        data={"video_url": "https://x.com/v"},
        files={"audio": ("a.mp3", b"d", "audio/mpeg")},
    )
    assert r.status_code == 400


def test_video_url_without_scheme_rejected(client):
    r = client.post("/api/v1/tasks", data={"video_url": "youtube.com/watch?v=x"})
    assert r.status_code == 400


def test_no_slides_flag_accepted(client):
    r = client.post(
        "/api/v1/tasks",
        data={"video_url": "https://youtu.be/abc", "no_slides": "true"},
    )
    assert r.status_code == 200
