from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from lecturelog.domain.media_source import MediaSource
from lecturelog.domain.models import Task
from lecturelog.domain.ports import SlideProvider

logger = logging.getLogger(__name__)


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

    async def _consume(self) -> None:
        while True:
            job = await self._queue.get()
            try:
                await self._service.run(
                    task=job.task,
                    source=job.source,
                    slide_provider=job.slide_provider,
                    work_dir=job.work_dir,
                    video_slide_provider_factory=job.video_slide_provider_factory,
                )
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
