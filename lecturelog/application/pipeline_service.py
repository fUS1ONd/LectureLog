from __future__ import annotations

import json
import logging
import traceback
from collections.abc import Callable
from pathlib import Path

from lecturelog.application.error_classifier import classify_error
from lecturelog.application.factories import cutter_factory
from lecturelog.application.progress_plan import ProgressPlan
from lecturelog.application.usage_accumulator import UsageAccumulator
from lecturelog.domain.enums import PipelineStage, TaskStatus
from lecturelog.domain.media_source import (
    AudioSource,
    MediaSource,
    S3ObjectSource,
    VideoFileSource,
    is_video_source,
)
from lecturelog.domain.models import Task
from lecturelog.domain.ports import (
    Exporter,
    MediaCutter,
    MediaIngestor,
    SlideImage,
    SlideProvider,
    Storage,
    Structurizer,
    TaskRepository,
    Transcriber,
    WebhookNotifier,
)
from lecturelog.infrastructure.export.structure import build_structure, result_key
from lecturelog.infrastructure.export.zip_utils import zip_dir
from lecturelog.infrastructure.frames.binding import bind_frames_to_sections
from lecturelog.infrastructure.frames.placement import place_slides_in_sections

logger = logging.getLogger(__name__)

# Терминальные статусы: только на них шлём вебхук платформе.
_TERMINAL = {TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.INTERRUPTED}


class PipelineService:
    def __init__(
        self,
        repository: TaskRepository,
        transcriber: Transcriber,
        structurizer: Structurizer,
        audio_cutter: MediaCutter,
        exporter: Exporter,
        progress_plan_factory: Callable[[], ProgressPlan],
        video_cutter: MediaCutter | None = None,
        ingestor: MediaIngestor | None = None,
        webhook_notifier: WebhookNotifier | None = None,
        storage: Storage | None = None,
    ):
        self._repo = repository
        self._transcriber = transcriber
        self._structurizer = structurizer
        self._audio_cutter = audio_cutter
        self._video_cutter = video_cutter
        self._ingestor = ingestor
        self._exporter = exporter
        self._plan_factory = progress_plan_factory
        # Опциональный нотификатор: None в автономном режиме (без PLATFORM_CALLBACK_URL).
        self._webhook = webhook_notifier
        # Хранилище лекций: исходник-внутрь (s3_object) и результат-наружу папкой
        # объектов под results/<task_id>/. None — автономия (результат: локальный zip).
        self._storage = storage

    async def _set(
        self,
        task: Task,
        *,
        status=None,
        stage=None,
        progress=None,
        error=None,
        error_code=None,
        result_path=None,
    ):
        if status is not None:
            task.status = status
        if stage is not None:
            task.stage = stage
        if progress is not None:
            task.progress_pct = progress
        if error is not None:
            task.error = error
        # Машинный код ошибки ставится только при наличии (на успешных стадиях остаётся None).
        if error_code is not None:
            task.error_code = error_code
        if result_path is not None:
            task.result_path = result_path
        await self._repo.update(task)

        # Пуш платформе только на терминальных статусах и только если нотификатор задан.
        # Best-effort: ошибка/таймаут логируется и НЕ роняет/не задерживает пайплайн
        # (защита от лежащей платформы; надёжность — на fallback-поллинге платформы).
        if status in _TERMINAL and self._webhook is not None:
            try:
                await self._webhook.notify(
                    task.task_id,
                    status,
                    error=task.error,
                    error_code=task.error_code.value if task.error_code else None,
                )
            except Exception as exc:  # noqa: BLE001 — намеренно глушим любой сбой нотификации
                logger.warning("Вебхук для task=%s не доставлен: %s", task.task_id, exc)

    async def _persist_usage(self, task: Task, acc: UsageAccumulator) -> None:
        """Гранулярность персиста = стадия: пересчитать total и сохранить usage.
        НЕ звать на каждый LLM-колбэк (иначе DB-шторм)."""
        acc.compute_total()
        task.usage = acc.usage
        await self._repo.update(task)

    async def run(
        self,
        task: Task,
        source: MediaSource,
        slide_provider: SlideProvider | None,
        work_dir: Path,
        video_slide_provider_factory: Callable[[Path, Path], SlideProvider] | None = None,
    ) -> str:
        # Граница S3-вход: если источник — ключ в MinIO, скачиваем его в локальный
        # scratch и нормализуем в обычный локальный Audio/VideoFile-источник.
        # Дальше пайплайн работает как с приложенным файлом (внутренние стадии не трогаем).
        if isinstance(source, S3ObjectSource):
            local_src = work_dir / "src" / Path(source.key).name
            await self._storage.download_file(source.key, local_src)
            if source.media == "video":
                source = VideoFileSource(path=local_src)
            else:
                source = AudioSource(path=local_src)

        is_video = is_video_source(source)
        plan = ProgressPlan.for_video() if is_video else self._plan_factory()

        # Накопитель расхода: source-ось известна сразу; slides_origin уточняется
        # после того, как определится фактически отработавший провайдер слайдов.
        acc = UsageAccumulator()
        acc.set_mode(source="video" if is_video else "audio", slides_origin="none")

        # Нейтральное зерно от провайдеров; стадию навешивают эти closure'ы.
        async def transcribe_usage(payload: dict):
            acc.record_transcribe(payload)

        async def structurize_usage(payload: dict):
            acc.record_llm("structurize", payload)

        try:
            # Видео: источник аудио для транскрибации — извлечённая дорожка,
            # источник для нарезки — скачанное/локальное видео. Для аудио оба = source.path.
            if is_video:
                await self._set(
                    task,
                    status=TaskStatus.PROCESSING,
                    stage=PipelineStage.VIDEO_INGEST,
                    progress=0,
                    error=None,
                )
                local_video = await self._ingestor.ingest(source, output_dir=work_dir / "video_src")

                await self._set(
                    task,
                    stage=PipelineStage.AUDIO_EXTRACT,
                    progress=plan.stage_start(PipelineStage.AUDIO_EXTRACT),
                )
                audio_for_transcribe = await self._ingestor.extract_audio(
                    local_video, output_dir=work_dir / "extracted_audio"
                )
                cut_source = local_video
                cut_stage = PipelineStage.VIDEO_CUT
            else:
                await self._set(
                    task,
                    status=TaskStatus.PROCESSING,
                    stage=PipelineStage.TRANSCRIBE,
                    progress=0,
                    error=None,
                )
                audio_for_transcribe = source.path
                cut_source = source.path
                cut_stage = PipelineStage.AUDIO_CUT

            await self._set(
                task,
                stage=PipelineStage.TRANSCRIBE,
                progress=plan.stage_start(PipelineStage.TRANSCRIBE),
            )

            async def transcribe_progress(pct: int):
                await self._set(
                    task,
                    stage=PipelineStage.TRANSCRIBE,
                    progress=plan.scale(PipelineStage.TRANSCRIBE, pct),
                )

            srt_path = await self._transcriber.transcribe(
                audio_path=audio_for_transcribe,
                output_dir=work_dir / "transcribe",
                on_progress=transcribe_progress,
                on_usage=transcribe_usage,
            )
            # Инкрементальный персист: transcribe доезжает ДО появления structurize.
            await self._persist_usage(task, acc)

            # Стадия кадров из видео: только когда нет документа (документ приоритетнее)
            # и источник — видео. Кадры НЕ влияют на структуризацию (дизайн §4):
            # привязка к секциям — после structurize по таймкодам.
            video_frames: list[SlideImage] = []
            if slide_provider is None and video_slide_provider_factory is not None and is_video:
                acc.set_mode(source="video", slides_origin="video_extracted")
                await self._set(
                    task,
                    stage=PipelineStage.VIDEO_SLIDES,
                    progress=plan.stage_start(PipelineStage.VIDEO_SLIDES),
                )

                async def frames_usage(payload: dict):
                    acc.record_llm("video_slides", payload)

                frames_provider = video_slide_provider_factory(local_video, srt_path)
                try:
                    video_frames = await frames_provider.get_slides(
                        output_dir=work_dir / "frames",
                        on_usage=frames_usage,
                    )
                except Exception as frames_error:  # noqa: BLE001 — стадия кадров
                    # никогда не роняет задачу (философия no_slides, дизайн §10)
                    logger.warning(
                        "Стадия кадров упала для task=%s, конспект без кадров: %s",
                        task.task_id,
                        frames_error,
                    )
                    video_frames = []
                await self._persist_usage(task, acc)

            slide_items: list[SlideImage] = []
            if slide_provider is not None:
                # Единственный источник документных слайдов — приложенный документ,
                # slides_origin — "document". Видео-кадры (см. выше) идут отдельным
                # путём и объединяются с slide_items только после structurize.
                acc.set_mode(
                    source="video" if is_video else "audio",
                    slides_origin="document",
                )
                await self._set(
                    task,
                    stage=PipelineStage.SLIDES,
                    progress=plan.stage_start(PipelineStage.SLIDES),
                )
                slide_items = await slide_provider.get_slides(
                    output_dir=work_dir / "slides",
                    on_usage=None,
                )

            await self._set(
                task,
                stage=PipelineStage.STRUCTURIZE,
                progress=plan.stage_start(PipelineStage.STRUCTURIZE),
            )

            async def structurize_progress(pct: int):
                await self._set(
                    task,
                    stage=PipelineStage.STRUCTURIZE,
                    progress=plan.scale(PipelineStage.STRUCTURIZE, pct),
                )

            topics = await self._structurizer.structurize(
                srt_path=srt_path,
                slide_images=[s.path for s in slide_items],  # только документ; кадры — мимо
                output_dir=work_dir / "structurize",
                on_progress=structurize_progress,
                on_usage=structurize_usage,
            )
            await self._persist_usage(task, acc)

            if video_frames:
                # G: привязка кадров к секциям по таймкодам + монотонизация
                bind_frames_to_sections(video_frames, topics)
                # Маркеры <!-- slide:N --> внутри content секций — позиция
                # кадра между абзацами (взвешенная пропорция по timestamp)
                place_slides_in_sections(video_frames, topics)
                slide_items = video_frames

            sections = [s for t in topics for s in t.sections]
            cutter = cutter_factory(
                source, audio_cutter=self._audio_cutter, video_cutter=self._video_cutter
            )
            await self._set(task, stage=cut_stage, progress=plan.stage_start(cut_stage))
            fragments = await cutter.cut(
                source_path=cut_source,
                sections=sections,
                output_dir=work_dir / ("video" if is_video else "audio"),
            )

            await self._set(
                task, stage=PipelineStage.EXPORT, progress=plan.stage_start(PipelineStage.EXPORT)
            )
            media_kind = "video" if is_video else "audio"
            export_result = await self._exporter.export(
                topics=topics,
                media_fragments=fragments,
                slide_images=slide_items,
                output_dir=work_dir / "export",
                media_kind=media_kind,
            )
            output_root = export_result.output_root

            # structure.json — нейтральное дерево с РЕАЛЬНЫМИ ключами MinIO.
            # Кладём в output_root, чтобы он попал и в пофайловую заливку, и в
            # автономный локальный zip.
            structure = build_structure(
                topics=topics,
                media_targets=export_result.media_targets,
                slide_targets=export_result.slide_targets,
                output_root=output_root,
                task_id=task.task_id,
                media_kind=media_kind,
            )
            (output_root / "structure.json").write_text(
                json.dumps(structure, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            # Граница S3-выход: результат — ПАПКА отдельных объектов под префиксом
            # results/<task_id>/, zip не хранится (собирается на лету при скачивании).
            # result_path хранит сам префикс. Без storage (автономия/unit-тесты) —
            # локальный zip из output/, result_path — путь к нему.
            if self._storage is not None:
                prefix = f"results/{task.task_id}/"
                # ЕДИНАЯ формула ключа (result_key) с build_structure -> ключи в
                # structure.json совпадают с реально залитыми объектами.
                for path in sorted(output_root.rglob("*")):
                    if path.is_file():
                        await self._storage.upload_file(
                            path, result_key(path, output_root, task.task_id)
                        )
                result_path = prefix
            else:
                local_zip = work_dir / "export" / "result.zip"
                zip_dir(output_root, local_zip, base=output_root.parent)
                result_path = str(local_zip)

            await self._set(
                task,
                status=TaskStatus.DONE,
                stage=PipelineStage.EXPORT,
                progress=100,
                result_path=result_path,
                error=None,
            )
            return result_path
        except Exception as exc:
            logger.warning("Пайплайн упал для task=%s: %s", task.task_id, exc)
            # Best-effort: пересчитать total и сохранить частичный расход,
            # чтобы он доехал на FAILED/INTERRUPTED.
            acc.compute_total()
            task.usage = acc.usage
            # Классифицируем исключение в машинный код для платформы (retry-решение).
            code = classify_error(exc)
            await self._set(
                task,
                status=TaskStatus.FAILED,
                error=f"{exc}\n{traceback.format_exc()}",
                error_code=code,
            )
            raise
