from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile

from lecturelog.config import Settings
from lecturelog.models import PipelineStatus
from lecturelog.pipeline.runner import PipelineRunner


class TaskManager:
    def __init__(self, settings: Settings, runner: PipelineRunner):
        self.settings = settings
        self.runner = runner
        self.statuses = runner.statuses

    async def create_task(self, audio: UploadFile, slides: UploadFile | None) -> str:
        task_id = uuid4().hex
        task_dir = Path(self.settings.UPLOAD_DIR) / task_id
        task_dir.mkdir(parents=True, exist_ok=True)

        audio_path = task_dir / (audio.filename or "audio.bin")
        audio_bytes = await audio.read()
        audio_path.write_bytes(audio_bytes)

        slides_path: Path | None = None
        if slides is not None:
            slides_path = task_dir / (slides.filename or "slides.bin")
            slides_bytes = await slides.read()
            slides_path.write_bytes(slides_bytes)

        asyncio.create_task(self.runner.run(task_id, audio_path, slides_path))
        self.statuses[task_id] = PipelineStatus(task_id=task_id, progress_pct=0)
        return task_id

    def get_status(self, task_id: str) -> PipelineStatus | None:
        return self.statuses.get(task_id)
