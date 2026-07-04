from __future__ import annotations

import shutil
from pathlib import Path, PurePosixPath
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.background import BackgroundTask

from lecturelog.api.dependencies import (
    get_cookie_store,
    get_presign_expiry,
    get_repository,
    get_storage,
    get_work_dir,
    get_worker,
)
from lecturelog.api.schemas import (
    CookieStatusResponse,
    CreateTaskResponse,
    ErrorResponse,
    ResultUrlResponse,
    TaskStatusResponse,
    TranscriptFailedError,
    TranscriptInvalidFormatError,
    TranscriptNotFoundError,
    UploadUrlRequest,
    UploadUrlResponse,
)
from lecturelog.application.use_cases.create_task import CreateTaskUseCase
from lecturelog.application.use_cases.delete_task import DeleteTaskUseCase
from lecturelog.application.use_cases.get_result import GetResultUseCase
from lecturelog.application.use_cases.get_status import GetStatusUseCase
from lecturelog.application.use_cases.get_transcript import GetTranscriptUseCase
from lecturelog.application.worker import PipelineJob
from lecturelog.domain.exceptions import ResultNotReady, TranscribeFailed
from lecturelog.domain.media_source import (
    AudioSource,
    S3ObjectSource,
    VideoFileSource,
    VideoUrlSource,
)
from lecturelog.infrastructure.export.zip_utils import zip_dir
from lecturelog.infrastructure.media.url_utils import is_url
from lecturelog.infrastructure.slides.document_provider import DocumentSlideProvider
from lecturelog.infrastructure.srt import srt_to_plain_text
from lecturelog.infrastructure.youtube.cookie_validation import (
    InvalidCookieFormat,
    validate_netscape_cookies,
)

router = APIRouter(prefix="/api/v1")

# Защита от заливки мусора: cookies.txt всегда мал (десятки КБ).
_MAX_COOKIE_BYTES = 1 * 1024 * 1024  # 1 МБ

# Размер чанка для стриминговой записи UploadFile на диск:
# .read() без аргумента грузит весь файл в RAM, что недопустимо на больших медиа.
_UPLOAD_CHUNK = 1024 * 1024


async def _save_upload(upload: UploadFile, target: Path) -> None:
    with target.open("wb") as f:
        while True:
            chunk = await upload.read(_UPLOAD_CHUNK)
            if not chunk:
                break
            f.write(chunk)


def _transcript_srt_path(work_dir: Path, task_id: str) -> Path:
    return work_dir / task_id / "transcribe" / "transcript.srt"


def _safe_filename(filename: str) -> str:
    # Берём только basename и режем потенциально опасные сепараторы,
    # чтобы клиент не задал ключ вне своего uploads/<uuid>/ префикса.
    name = Path(filename).name.replace("\\", "_")
    return name or "upload.bin"


async def _assemble_result_zip(storage, prefix: str, dest_dir: Path) -> Path:
    """Собрать zip результата НА ЛЕТУ из объектов под префиксом results/<id>/.

    Листит ключи, скачивает их в dest_dir/output (arcname без префикса -> output/...),
    зипует. Пустой листинг -> ResultNotReady (объекты ещё не залиты). dest_dir уникален
    на запрос (вызывающий чистит его целиком). Возвращает путь к собранному zip.
    """
    keys = await storage.list_keys(prefix)
    if not keys:
        raise ResultNotReady(prefix)
    src_root = dest_dir / "src"
    for key in keys:
        rel = key[len(prefix) :]  # results/<id>/output/... -> output/...
        await storage.download_file(key, src_root / rel)
    zip_path = dest_dir / "result.zip"
    zip_dir(src_root / "output", zip_path, base=src_root)
    return zip_path


@router.post(
    "/tasks",
    response_model=CreateTaskResponse,
    summary="Создать задачу обработки лекции",
    description="Принимает ровно один источник: audio | video | video_url | s3_key.",
    tags=["tasks"],
    responses={
        400: {"model": ErrorResponse, "description": "Некорректный или неоднозначный источник"}
    },
)
async def create_task(
    request: Request,
    audio: Annotated[UploadFile | None, File()] = None,
    video: Annotated[UploadFile | None, File()] = None,
    video_url: Annotated[str | None, Form()] = None,
    s3_key: Annotated[str | None, Form()] = None,
    media: Annotated[str | None, Form()] = None,
    slides: Annotated[UploadFile | None, File()] = None,
    no_slides: Annotated[bool, Form()] = False,
    repository=Depends(get_repository),
    worker=Depends(get_worker),
    work_dir: Path = Depends(get_work_dir),
):
    sources_count = sum(x is not None for x in (audio, video, video_url, s3_key))
    if sources_count != 1:
        return JSONResponse(
            status_code=400,
            content={"detail": "Specify exactly one of: audio, video, video_url, s3_key"},
        )
    if video_url is not None and not is_url(video_url):
        return JSONResponse(
            status_code=400,
            content={"detail": "video_url должен быть http/https URL"},
        )
    if s3_key is not None and (media or "audio") not in ("audio", "video"):
        return JSONResponse(
            status_code=400,
            content={"detail": "media должен быть audio или video"},
        )
    # Инвариант «исходник-внутрь только через uploads/»: клиент не должен иметь
    # возможности протащить чужой/произвольный ключ бакета (IDOR) или выйти за
    # пределы uploads/ через traversal-сегменты (..).
    if s3_key is not None and (
        ".." in PurePosixPath(s3_key).parts or not s3_key.startswith("uploads/")
    ):
        return JSONResponse(
            status_code=400,
            content={"detail": "s3_key должен быть в uploads/"},
        )

    media_upload = audio if audio is not None else video

    async def enqueue(task_id: str) -> None:
        task_dir = work_dir / task_id
        task_dir.mkdir(parents=True, exist_ok=True)

        # Источник: сохраняем загруженный файл (audio/video) на диск; для video_url
        # файла нет — его скачает ingestor внутри pipeline; для s3_key исходник уже
        # в MinIO (uploads/) — его скачает pipeline по ключу.
        if s3_key is not None:
            source = S3ObjectSource(key=s3_key, media=media or "audio")
        elif video_url is not None:
            source = VideoUrlSource(url=video_url)
        else:
            media_path = task_dir / (media_upload.filename or "media.bin")
            await _save_upload(media_upload, media_path)
            source = (
                VideoFileSource(path=media_path)
                if video is not None
                else AudioSource(path=media_path)
            )

        # Документ-провайдер можно собрать сразу (файл приложен).
        document_provider = None
        if slides is not None:
            slides_path = task_dir / (slides.filename or "slides.bin")
            await _save_upload(slides, slides_path)
            document_provider = DocumentSlideProvider(slides_path=slides_path)

        # Извлечение слайдов из видео отключено (тихая деградация, см. дизайн фаза 2:
        # VideoSlideProvider завязан на старый genai-контракт, а конфиг видео-моделей
        # удалён вместе с переходом на LlmClient/OpenRouter). Видео обрабатывается как
        # аудио-лекция без слайдов; удаление кода VideoSlideProvider — фаза 3.
        video_slide_provider_factory = None

        # Три режима слайдов: no_slides гасит оба провайдера; документ приоритетнее
        # видео; отложенный видео-провайдер строит pipeline после ingest.
        if no_slides:
            document_provider = None
            video_slide_provider_factory = None

        task = await repository.get(task_id)
        await worker.enqueue(
            PipelineJob(
                task_id=task_id,
                task=task,
                source=source,
                slide_provider=document_provider,
                work_dir=task_dir,
                video_slide_provider_factory=video_slide_provider_factory,
            )
        )

    # Источник для use-case нужен только ради source.kind (Task.source_kind);
    # реальные пути собираются в enqueue, когда известен task_id.
    if s3_key is not None:
        kind_source = S3ObjectSource(key=s3_key, media=media or "audio")
    elif video_url is not None:
        kind_source = VideoUrlSource(url=video_url)
    elif video is not None:
        kind_source = VideoFileSource(path=Path("placeholder.bin"))
    else:
        kind_source = AudioSource(path=Path("placeholder.bin"))

    use_case = CreateTaskUseCase(repository=repository, enqueue=enqueue)
    # source_key сохраняем только для s3-источника (uploads/...), чтобы DELETE
    # позже мог удалить исходник; для остальных источников исходного объекта в
    # MinIO нет.
    task_id = await use_case.execute(
        source=kind_source,
        slides_path=None,
        source_key=s3_key if s3_key is not None else None,
    )
    return CreateTaskResponse(task_id=task_id)


@router.post(
    "/uploads",
    response_model=UploadUrlResponse,
    summary="Получить presigned PUT для загрузки исходника",
    tags=["tasks"],
    responses={
        400: {"model": ErrorResponse, "description": "Некорректный источник (InvalidSource)"},
        409: {
            "model": ErrorResponse,
            "description": "Presigned upload недоступен: S3_PUBLIC_ENDPOINT не задан",
        },
    },
)
async def create_upload_url(
    body: UploadUrlRequest,
    storage=Depends(get_storage),
    expires_in: int = Depends(get_presign_expiry),
):
    # Презентуем платформе presigned PUT в uploads/<uuid>/<safe-filename>.
    key = f"uploads/{uuid4().hex}/{_safe_filename(body.filename)}"
    url = await storage.presigned_put(key, expires_in=expires_in)
    if url is None:
        return JSONResponse(
            status_code=409,
            content={"detail": "presigned upload недоступен: S3_PUBLIC_ENDPOINT не задан"},
        )
    return UploadUrlResponse(key=key, url=url, expires_in=expires_in)


@router.get(
    "/tasks/{task_id}",
    response_model=TaskStatusResponse,
    summary="Статус задачи и накопленный usage",
    tags=["tasks"],
    responses={404: {"model": ErrorResponse, "description": "Task not found"}},
)
async def get_task_status(task_id: str, repository=Depends(get_repository)):
    use_case = GetStatusUseCase(repository=repository)
    task = await use_case.execute(task_id)
    # response_model=TaskStatusResponse оставлен ТОЛЬКО для OpenAPI-схемы
    # (типизированная Usage документирует контракт). Фактически отдаём usage
    # сырым passthrough'ом из task.usage — байт-в-байт как писал аккумулятор и
    # как отдавал старый роут. Это сохраняет wire-формат: ключ transcribe.model:null
    # не теряется (response_model_exclude_none рекурсивно резал его), пустой usage
    # остаётся пустым объектом, отсутствующие стадии не материализуются как null.
    # Побочно: путь чтения статуса не валидирует usage через Usage и не падает 500
    # при неожиданной форме usage (NON-BLOCKING 4).
    return JSONResponse(content=TaskStatusResponse.wire_body(task))


@router.delete(
    "/tasks/{task_id}",
    status_code=204,
    summary="Идемпотентно удалить задачу и её объекты MinIO",
    description=(
        "Удаляет объекты ядра в MinIO (results/<task_id>/ и связанный uploads/<...>) "
        "и строку задачи. Идемпотентно: повтор на уже удалённую/неизвестную задачу -> 204."
    ),
    tags=["tasks"],
)
async def delete_task(
    task_id: str,
    repository=Depends(get_repository),
    storage=Depends(get_storage),
):
    # За HMAC-контуром платформа<->ядро (внутренний контракт, как POST /tasks).
    # Платформа ретраит при обрыве -> use-case идемпотентен, всегда 204.
    use_case = DeleteTaskUseCase(repository=repository, storage=storage)
    await use_case.execute(task_id)
    return Response(status_code=204)


@router.get(
    "/tasks/{task_id}/transcript",
    summary="Скачать транскрипт (srt|txt)",
    tags=["tasks"],
    responses={
        200: {
            "content": {"application/x-subrip": {}, "text/plain": {}},
            "description": "Готовый транскрипт файлом",
        },
        202: {"description": "Транскрипт ещё не готов, повторить позже"},
        # Этот роут отдаёт ошибки в форме {"error": ...}, а не {"detail": ...},
        # поэтому документируем фактические модели, а не общий ErrorResponse.
        400: {
            "model": TranscriptInvalidFormatError,
            "description": "Недопустимый format",
        },
        404: {"model": TranscriptNotFoundError, "description": "Задача не найдена"},
        409: {
            "model": TranscriptFailedError,
            "description": "Транскрибация завершилась ошибкой",
        },
    },
)
async def get_task_transcript(
    task_id: str,
    format: str = "srt",
    repository=Depends(get_repository),
    work_dir: Path = Depends(get_work_dir),
):
    # Валидация формата заранее, до проверки статуса задачи.
    if format not in ("srt", "txt"):
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_format", "allowed": ["srt", "txt"]},
        )

    use_case = GetTranscriptUseCase(
        repository=repository,
        srt_path_for=lambda tid: _transcript_srt_path(work_dir, tid),
    )
    task = await repository.get(task_id)
    if task is None:
        return JSONResponse(status_code=404, content={"error": "task_not_found"})

    try:
        result = await use_case.execute(task_id)
    except TranscribeFailed:
        return JSONResponse(
            status_code=409,
            content={"error": "transcribe_failed", "detail": task.error},
        )

    if not result.ready:
        return JSONResponse(
            status_code=202,
            content={
                "status": "in_progress",
                "stage": result.stage.value if result.stage else None,
                "progress": result.progress_pct,
                "message": "Transcript not ready yet, retry later",
            },
        )

    if format == "srt":
        return FileResponse(
            path=result.path,
            filename="transcript.srt",
            media_type="application/x-subrip",
        )

    plain = srt_to_plain_text(result.path.read_text(encoding="utf-8"))
    return Response(
        content=plain,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="transcript.txt"'},
    )


@router.get(
    "/tasks/{task_id}/result",
    summary="Собрать и отдать ZIP-результат на лету",
    tags=["tasks"],
    responses={
        200: {"content": {"application/zip": {}}, "description": "ZIP с результатом"},
        404: {"model": ErrorResponse, "description": "Задача не найдена или результат не готов"},
    },
)
async def get_task_result(
    task_id: str,
    repository=Depends(get_repository),
    storage=Depends(get_storage),
    work_dir: Path = Depends(get_work_dir),
):
    # Папочный результат: листим объекты под results/<id>/, скачиваем во временный
    # uuid-подкаталог и собираем zip на лету (zip не хранится в MinIO — нет дублирования).
    # MinIO клиенту не виден — работает даже без public endpoint (дефолт автономии).
    use_case = GetResultUseCase(repository=repository)
    prefix = await use_case.execute(task_id)
    # Уникальный каталог на запрос: параллельные обращения к одному task_id не мешают друг другу.
    tmp_dir = work_dir / "results_tmp" / task_id / uuid4().hex
    try:
        zip_path = await _assemble_result_zip(storage, prefix, tmp_dir)
    except Exception:
        # На пути ошибки (download_file упал в середине сборки и т.п.) FileResponse
        # с BackgroundTask не создаётся — чистим tmp-каталог сразу, иначе disk leak.
        # Поведение ошибки для клиента не меняем: пробрасываем исключение дальше.
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    # На успехе каталог должен дожить до стрима — удаляем его через BackgroundTask
    # ПОСЛЕ отдачи файла (иначе disk leak на каждый запрос).
    return FileResponse(
        path=zip_path,
        filename="result.zip",
        media_type="application/zip",
        background=BackgroundTask(lambda: shutil.rmtree(tmp_dir, ignore_errors=True)),
    )


@router.get(
    "/tasks/{task_id}/result-url",
    response_model=ResultUrlResponse,
    summary="Presigned GET на готовый ZIP",
    tags=["tasks"],
    responses={
        404: {"model": ErrorResponse, "description": "Задача не найдена или результат не готов"},
        409: {
            "model": ErrorResponse,
            "description": "Presigned download недоступен: S3_PUBLIC_ENDPOINT не задан",
        },
    },
)
async def get_task_result_url(
    task_id: str,
    filename: str = "result",
    repository=Depends(get_repository),
    storage=Depends(get_storage),
    work_dir: Path = Depends(get_work_dir),
):
    # Папочный результат: собираем zip на лету тем же helper'ом, что /result, заливаем
    # ВРЕМЕННЫМ объектом results-tmp/<id>/<uuid>.zip и выдаём presigned GET (1 час) с
    # override-заголовками (attachment; filename="X.zip" + zip). tmp-объект чистится
    # lifecycle MinIO (префикс results-tmp/) и DELETE. Без public endpoint -> 409.
    use_case = GetResultUseCase(repository=repository)
    prefix = await use_case.execute(task_id)
    tmp_dir = work_dir / "results_tmp" / task_id / uuid4().hex
    try:
        zip_path = await _assemble_result_zip(storage, prefix, tmp_dir)
        tmp_key = f"results-tmp/{task_id}/{uuid4().hex}.zip"
        await storage.upload_file(zip_path, tmp_key)
    finally:
        # Локальный zip нужен только для заливки — чистим весь tmp-каталог движка.
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # expires_in фиксирован 1 час (tmp-объект живёт коротко: lifecycle 1 день).
    expires_in = 3600
    url = await storage.presigned_get(
        tmp_key,
        expires_in=expires_in,
        download_filename=filename,
        content_type="application/zip",
    )
    if url is None:
        # tmp-zip уже залит — допустимый orphan (подберёт lifecycle/DELETE).
        return JSONResponse(
            status_code=409,
            content={"detail": "presigned download недоступен: S3_PUBLIC_ENDPOINT не задан"},
        )
    return ResultUrlResponse(url=url, expires_in=expires_in)


@router.get("/health", summary="Проверка живости сервиса", tags=["health"])
async def health():
    return {"status": "ok"}


@router.get(
    "/youtube/cookies",
    response_model=CookieStatusResponse,
    summary="Статус YouTube-cookies (без содержимого)",
    tags=["youtube"],
)
async def get_youtube_cookies(cookie_store=Depends(get_cookie_store)):
    st = await cookie_store.status()
    return CookieStatusResponse(exists=st.exists, updated_at=st.updated_at, size=st.size)


@router.put(
    "/youtube/cookies",
    response_model=CookieStatusResponse,
    summary="Загрузить/заменить YouTube-cookies (cookies.txt)",
    tags=["youtube"],
    responses={
        400: {"model": ErrorResponse, "description": "Некорректный формат cookies"},
        413: {"model": ErrorResponse, "description": "Файл слишком большой"},
    },
)
async def put_youtube_cookies(
    file: Annotated[UploadFile, File()],
    cookie_store=Depends(get_cookie_store),
):
    content = await file.read()
    if len(content) > _MAX_COOKIE_BYTES:
        return JSONResponse(status_code=413, content={"detail": "Файл cookies слишком большой"})
    try:
        validate_netscape_cookies(content)
    except InvalidCookieFormat as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})
    st = await cookie_store.save(content)
    return CookieStatusResponse(exists=st.exists, updated_at=st.updated_at, size=st.size)


@router.delete(
    "/youtube/cookies",
    status_code=204,
    summary="Удалить YouTube-cookies (вернуться к анонимному режиму)",
    tags=["youtube"],
)
async def delete_youtube_cookies(cookie_store=Depends(get_cookie_store)):
    await cookie_store.delete()
    return Response(status_code=204)
