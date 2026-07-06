import pytest
from fastapi.testclient import TestClient

from lecturelog.api import dependencies as deps
from lecturelog.api.app import create_app
from lecturelog.application.usage_accumulator import UsageAccumulator
from lecturelog.domain.enums import PipelineStage, TaskStatus
from lecturelog.domain.models import Task
from tests.support.fake_storage import FakeStorage


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

    async def delete(self, tid):
        self.tasks.pop(tid, None)


class NoopWorker:
    def __init__(self):
        self.jobs = []

    async def enqueue(self, job):
        self.jobs.append(job)


@pytest.fixture
def repo():
    return InMemoryRepo()


def _build_client(repo, tmp_path, storage):
    # Собираем приложение без реального lifespan: вешаем зависимости
    # через dependency_overrides, чтобы тест проверял HTTP-контракт,
    # а не реальную обработку (Groq/Gemini/Postgres).
    app = create_app()
    worker = NoopWorker()
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
    app.state.frames_provider_factory = None
    client = TestClient(app)
    client._worker = worker
    client._storage = storage
    return client


@pytest.fixture
def client(repo, tmp_path):
    # Дефолт автономии: public=False (presigned наружу выключен).
    return _build_client(repo, tmp_path, FakeStorage(public=False))


@pytest.fixture
def client_public(repo, tmp_path):
    # Платформенный режим: public=True (presigned PUT/GET доступны).
    return _build_client(repo, tmp_path, FakeStorage(public=True))


def test_health_ok(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_create_requires_exactly_one_source(client):
    r = client.post("/api/v1/tasks")  # ни одного источника
    assert r.status_code == 400


def test_create_audio_returns_task_id(client):
    r = client.post("/api/v1/tasks", files={"audio": ("a.mp3", b"data", "audio/mpeg")})
    assert r.status_code == 200
    assert "task_id" in r.json()


def test_create_with_s3_key_creates_s3_object_source(client):
    r = client.post(
        "/api/v1/tasks",
        data={"s3_key": "uploads/abc/lecture.mp3", "media": "audio"},
    )
    assert r.status_code == 200
    job = client._worker.jobs[-1]
    assert job.source.kind == "s3_object"
    assert job.source.key == "uploads/abc/lecture.mp3"
    assert job.source.media == "audio"


def test_create_with_s3_key_persists_source_key(client, repo):
    r = client.post(
        "/api/v1/tasks",
        data={"s3_key": "uploads/abc/lecture.mp3", "media": "audio"},
    )
    assert r.status_code == 200
    task_id = r.json()["task_id"]
    assert repo.tasks[task_id].source_key == "uploads/abc/lecture.mp3"


def test_create_audio_has_no_source_key(client, repo):
    r = client.post("/api/v1/tasks", files={"audio": ("a.mp3", b"d", "audio/mpeg")})
    task_id = r.json()["task_id"]
    assert repo.tasks[task_id].source_key is None


def test_create_with_s3_key_video(client):
    r = client.post(
        "/api/v1/tasks",
        data={"s3_key": "uploads/abc/lec.mp4", "media": "video"},
    )
    assert r.status_code == 200
    job = client._worker.jobs[-1]
    assert job.source.kind == "s3_object"
    assert job.source.media == "video"


def test_s3_key_and_file_together_is_400(client):
    r = client.post(
        "/api/v1/tasks",
        data={"s3_key": "uploads/abc/lecture.mp3", "media": "audio"},
        files={"audio": ("a.mp3", b"d", "audio/mpeg")},
    )
    assert r.status_code == 400


def test_s3_key_invalid_media_is_400(client):
    r = client.post(
        "/api/v1/tasks",
        data={"s3_key": "uploads/abc/lecture.mp3", "media": "doc"},
    )
    assert r.status_code == 400


def test_s3_key_outside_uploads_is_400(client):
    # IDOR: чужой результат вне uploads/ нельзя протащить в источник.
    r = client.post(
        "/api/v1/tasks",
        data={"s3_key": "results/other/result.zip", "media": "audio"},
    )
    assert r.status_code == 400
    assert "uploads/" in r.json()["detail"]
    assert client._worker.jobs == []


def test_s3_key_with_traversal_is_400(client):
    # Path traversal: сегмент .. позволяет выйти за пределы uploads/.
    r = client.post(
        "/api/v1/tasks",
        data={"s3_key": "uploads/../results/x", "media": "audio"},
    )
    assert r.status_code == 400
    assert "uploads/" in r.json()["detail"]
    assert client._worker.jobs == []


def test_uploads_returns_presigned_put(client_public):
    r = client_public.post("/api/v1/uploads", json={"filename": "lecture.mp3"})
    assert r.status_code == 200
    body = r.json()
    assert body["key"].startswith("uploads/")
    assert body["key"].endswith("/lecture.mp3")
    assert body["url"].startswith("https://fake/")
    assert body["key"] in body["url"]
    assert "expires_in" in body


def test_uploads_409_without_public(client):
    r = client.post("/api/v1/uploads", json={"filename": "lecture.mp3"})
    assert r.status_code == 409


def test_status_404_for_unknown(client):
    r = client.get("/api/v1/tasks/nonexistent")
    assert r.status_code == 404
    assert r.json()["detail"] == "Task not found"


def test_status_returns_fields(client, repo):
    repo.tasks["t"] = Task(
        task_id="t",
        source_kind="audio",
        status=TaskStatus.PROCESSING,
        stage=PipelineStage.STRUCTURIZE,
        progress_pct=55,
        usage={"transcribe": {"audio_seconds": 90, "provider": "groq", "raw": {}}},
    )
    r = client.get("/api/v1/tasks/t")
    assert r.status_code == 200
    body = r.json()
    assert body["task_id"] == "t"
    assert body["stage"] == "structurize"
    assert body["progress_pct"] == 55
    assert body["usage"] == {"transcribe": {"audio_seconds": 90, "provider": "groq", "raw": {}}}


def test_status_usage_wire_identical_to_accumulator(client, repo):
    # Реальный выход аккумулятора: transcribe пишет "model": None ВСЕГДА,
    # structurize по by_model, total с осями режима. GET /tasks/{id} обязан
    # отдавать usage БАЙТ-В-БАЙТ как он лежит в task.usage (как делал старый роут,
    # отдававший task.usage напрямую). В частности transcribe.model:null НЕ должен
    # пропадать из-за response_model_exclude_none.
    acc = UsageAccumulator()
    acc.set_mode("audio", "document")
    # provider/model без явного model -> payload.get("model") == None.
    acc.record_transcribe({"audio_seconds": 120, "provider": "groq"})
    acc.record_llm("structurize", {"model": "gemini-3", "prompt": 100, "output": 40})
    acc.compute_total()
    expected_usage = acc.usage
    # Инвариант реального выхода: ключ model присутствует и равен None.
    assert expected_usage["transcribe"]["model"] is None

    repo.tasks["t"] = Task(
        task_id="t",
        source_kind="audio",
        status=TaskStatus.PROCESSING,
        stage=PipelineStage.STRUCTURIZE,
        usage=expected_usage,
    )
    r = client.get("/api/v1/tasks/t")
    assert r.status_code == 200
    # Тело usage идентично исходному dict аккумулятора, включая "model": null.
    assert r.json()["usage"] == expected_usage


def test_status_empty_usage_is_empty_object(client, repo):
    # Пустой usage ({}) должен сериализоваться как пустой объект (как раньше),
    # а не как Usage() со всеми None/дефолтными ключами.
    repo.tasks["t"] = Task(task_id="t", source_kind="audio", usage={})
    r = client.get("/api/v1/tasks/t")
    assert r.status_code == 200
    assert r.json()["usage"] == {}


def test_transcript_invalid_format_400(client):
    r = client.get("/api/v1/tasks/whatever/transcript?format=pdf")
    assert r.status_code == 400
    assert r.json() == {"error": "invalid_format", "allowed": ["srt", "txt"]}


def test_transcript_task_not_found_404(client):
    r = client.get("/api/v1/tasks/missing/transcript")
    assert r.status_code == 404
    assert r.json() == {"error": "task_not_found"}


def test_transcript_failed_on_transcribe_409(client, repo):
    repo.tasks["t"] = Task(
        task_id="t",
        source_kind="audio",
        status=TaskStatus.FAILED,
        stage=PipelineStage.TRANSCRIBE,
        error="groq down",
    )
    r = client.get("/api/v1/tasks/t/transcript")
    assert r.status_code == 409
    assert r.json()["error"] == "transcribe_failed"


def test_transcript_in_progress_202(client, repo):
    repo.tasks["t"] = Task(
        task_id="t",
        source_kind="audio",
        status=TaskStatus.PROCESSING,
        stage=PipelineStage.TRANSCRIBE,
        progress_pct=10,
    )
    r = client.get("/api/v1/tasks/t/transcript")
    assert r.status_code == 202
    assert r.json()["status"] == "in_progress"


def test_transcript_srt_ready(client, repo, tmp_path):
    repo.tasks["t"] = Task(
        task_id="t",
        source_kind="audio",
        status=TaskStatus.PROCESSING,
        stage=PipelineStage.STRUCTURIZE,
    )
    srt_dir = tmp_path / "t" / "transcribe"
    srt_dir.mkdir(parents=True)
    (srt_dir / "transcript.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nhi\n", encoding="utf-8"
    )
    r = client.get("/api/v1/tasks/t/transcript?format=srt")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/x-subrip")


def test_result_not_ready_404(client, repo):
    repo.tasks["t"] = Task(task_id="t", source_kind="audio")
    r = client.get("/api/v1/tasks/t/result")
    assert r.status_code == 404
    assert r.json()["detail"] == "Result is not ready"


def _put_result_objects(storage, task_id="t"):
    # Раскладываем папку результата объектами (как pipeline после export).
    storage.objects[f"results/{task_id}/output/конспект.md"] = b"# md"
    storage.objects[f"results/{task_id}/output/audio/01-a.mp3"] = b"audio"
    storage.objects[f"results/{task_id}/output/structure.json"] = b"{}"


def _done_task(task_id="t"):
    return Task(
        task_id=task_id,
        source_kind="audio",
        status=TaskStatus.DONE,
        result_path=f"results/{task_id}/",
    )


def test_result_assembles_zip_from_objects(client, repo):
    # result_path — префикс папки; эндпоинт листит объекты, скачивает и собирает zip на лету.
    import io
    import zipfile

    _put_result_objects(client._storage)
    repo.tasks["t"] = _done_task()
    r = client.get("/api/v1/tasks/t/result")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        names = set(zf.namelist())
    assert "output/конспект.md" in names
    assert "output/audio/01-a.mp3" in names
    assert "output/structure.json" in names


def test_result_cleans_up_tmp_dir(client, repo, tmp_path):
    # Disk leak: временные каталоги сборки zip должны подчищаться после отдачи.
    _put_result_objects(client._storage)
    repo.tasks["t"] = _done_task()
    results_tmp = tmp_path / "results_tmp"
    for _ in range(3):
        r = client.get("/api/v1/tasks/t/result")
        assert r.status_code == 200
    leftover = list(results_tmp.rglob("*")) if results_tmp.exists() else []
    assert [p for p in leftover if p.is_file()] == []


def test_result_cleans_up_tmp_dir_on_assembly_error(client, repo, tmp_path):
    # Disk leak на пути ошибки: если download_file падает в середине сборки zip
    # (уже создан tmp-каталог с частью объектов), временный каталог не должен
    # оставаться на диске — на ошибке чистим сразу, не дожидаясь BackgroundTask.
    storage = client._storage
    _put_result_objects(storage)
    repo.tasks["t"] = _done_task()

    original_download = storage.download_file
    calls = {"n": 0}

    async def failing_download(key, local_path):
        # Первый объект скачиваем (tmp-каталог наполняется), на втором — падаем.
        calls["n"] += 1
        if calls["n"] >= 2:
            raise RuntimeError("download boom")
        await original_download(key, local_path)

    storage.download_file = failing_download

    # Поведение ошибки для клиента не меняем: исключение пробрасывается.
    with pytest.raises(RuntimeError, match="download boom"):
        client.get("/api/v1/tasks/t/result")

    results_tmp = tmp_path / "results_tmp"
    leftover = list(results_tmp.rglob("*")) if results_tmp.exists() else []
    assert [p for p in leftover if p.is_file()] == []


def test_result_not_ready_no_objects_404(client, repo):
    # result_path есть, но объектов под префиксом нет -> 404.
    repo.tasks["t"] = _done_task()
    r = client.get("/api/v1/tasks/t/result")
    assert r.status_code == 404
    assert r.json()["detail"] == "Result is not ready"


def test_result_url_assembles_and_presigns_tmp_object(client_public, repo):
    _put_result_objects(client_public._storage)
    repo.tasks["t"] = _done_task()
    r = client_public.get("/api/v1/tasks/t/result-url?filename=Лекция")
    assert r.status_code == 200
    body = r.json()
    assert body["url"].startswith("https://fake/")
    assert body["expires_in"] == 3600
    tmp_keys = [
        k
        for k in client_public._storage.objects
        if k.startswith("results-tmp/t/") and k.endswith(".zip")
    ]
    assert len(tmp_keys) == 1
    assert tmp_keys[0] in body["url"]
    assert "Лекция.zip" in body["url"]


def test_result_url_409_without_public(client, repo):
    _put_result_objects(client._storage)
    repo.tasks["t"] = _done_task()
    r = client.get("/api/v1/tasks/t/result-url?filename=Лекция")
    assert r.status_code == 409


def test_result_url_404_no_result(client_public, repo):
    repo.tasks["t"] = Task(task_id="t", source_kind="audio")
    r = client_public.get("/api/v1/tasks/t/result-url?filename=X")
    assert r.status_code == 404


def test_result_url_unique_tmp_key_per_request(client_public, repo):
    _put_result_objects(client_public._storage)
    repo.tasks["t"] = _done_task()
    client_public.get("/api/v1/tasks/t/result-url?filename=X")
    client_public.get("/api/v1/tasks/t/result-url?filename=X")
    tmp_keys = [
        k
        for k in client_public._storage.objects
        if k.startswith("results-tmp/t/") and k.endswith(".zip")
    ]
    assert len(tmp_keys) == 2


def test_delete_existing_returns_204_and_cleans_all(client, repo):
    repo.tasks["t"] = Task(
        task_id="t",
        source_kind="s3_object",
        source_key="uploads/u/lec.mp3",
        status=TaskStatus.DONE,
        result_path="results/t/",
    )
    client._storage.objects["results/t/output/конспект.md"] = b"md"
    client._storage.objects["results/t/output/audio/0.mp3"] = b"m"
    client._storage.objects["results-tmp/t/abc.zip"] = b"tmp"
    client._storage.objects["uploads/u/lec.mp3"] = b"src"

    r = client.delete("/api/v1/tasks/t")
    assert r.status_code == 204
    assert r.content == b""
    assert client._storage.objects == {}
    assert "t" not in repo.tasks


def test_delete_is_idempotent_returns_204_on_repeat(client, repo):
    repo.tasks["t"] = Task(task_id="t", source_kind="audio")
    client._storage.objects["results/t/output/конспект.md"] = b"md"
    assert client.delete("/api/v1/tasks/t").status_code == 204
    # Повтор на уже удалённую задачу: 204, НЕ 404/500.
    assert client.delete("/api/v1/tasks/t").status_code == 204


def test_delete_unknown_task_returns_204(client):
    # Никогда не создавалась -> всё равно 2xx (платформенный ретрай безопасен).
    r = client.delete("/api/v1/tasks/never-existed")
    assert r.status_code == 204


def test_delete_audio_task_keeps_foreign_uploads(client, repo):
    repo.tasks["t"] = Task(task_id="t", source_kind="audio")  # без source_key
    client._storage.objects["results/t/output/конспект.md"] = b"md"
    client._storage.objects["uploads/other/keep.mp3"] = b"keep"
    assert client.delete("/api/v1/tasks/t").status_code == 204
    assert "results/t/output/конспект.md" not in client._storage.objects
    assert "uploads/other/keep.mp3" in client._storage.objects
