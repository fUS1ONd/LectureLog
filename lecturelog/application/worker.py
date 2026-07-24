from __future__ import annotations

import asyncio
import logging
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from lecturelog.domain.media_source import MediaSource
from lecturelog.domain.models import Task
from lecturelog.domain.ports import SlideProvider

logger = logging.getLogger(__name__)

_TASK_ID_RE = re.compile(r"[0-9a-f]{32}\Z")


@dataclass
class PipelineJob:
    task_id: str
    task: Task
    source: MediaSource
    slide_provider: SlideProvider | None
    work_dir: Path
    # Отложенный видео-провайдер: строится после transcribe из (video_path, srt_path) —
    # стадии кадров нужен транскрипт (оракул live-coding, легенда QC).
    video_slide_provider_factory: Callable[[Path, Path], SlideProvider] | None = None


class PipelineWorker:
    def __init__(self, service, concurrency: int):
        self._service = service
        self._concurrency = concurrency
        self._queue: asyncio.Queue[PipelineJob] = asyncio.Queue()
        self._consumers: list[asyncio.Task] = []

    async def start(self) -> None:
        self._consumers = [asyncio.create_task(self._consume()) for _ in range(self._concurrency)]

    async def enqueue(self, job: PipelineJob) -> None:
        await self._queue.put(job)

    async def _cleanup_completed_work_dir(self, job: PipelineJob, result_path: str) -> None:
        """Best-effort удалить scratch только после подтверждённого S3-результата."""
        if result_path != f"results/{job.task_id}/":
            return
        if not _TASK_ID_RE.fullmatch(job.task_id) or job.work_dir.name != job.task_id:
            logger.error(
                "Воркер: небезопасный work_dir для cleanup task=%s path=%s",
                job.task_id,
                job.work_dir,
            )
            return
        if job.work_dir.is_symlink():
            logger.error("Воркер: symlink work_dir не удалён task=%s", job.task_id)
            return

        try:
            await asyncio.to_thread(shutil.rmtree, job.work_dir)
        except FileNotFoundError:
            return
        except OSError as exc:
            # Cleanup не должен менять уже сохранённую DONE-задачу на FAILED.
            logger.warning(
                "Воркер: не удалось очистить work_dir task=%s path=%s: %s",
                job.task_id,
                job.work_dir,
                exc,
            )
        else:
            logger.info("Воркер: очищен work_dir завершённой задачи task=%s", job.task_id)

    async def _consume(self) -> None:
        while True:
            job = await self._queue.get()
            try:
                result_path = await self._service.run(
                    task=job.task,
                    source=job.source,
                    slide_provider=job.slide_provider,
                    work_dir=job.work_dir,
                    video_slide_provider_factory=job.video_slide_provider_factory,
                )
                await self._cleanup_completed_work_dir(job, result_path)
            except Exception as exc:  # задача уже помечена FAILED в repo
                logger.warning("Воркер: задача %s завершилась ошибкой: %s", job.task_id, exc)
            finally:
                self._queue.task_done()

    async def stop(self) -> None:
        await self._queue.join()  # дождаться обработки всех заданий
        for c in self._consumers:
            c.cancel()
        await asyncio.gather(*self._consumers, return_exceptions=True)
        self._consumers = []
