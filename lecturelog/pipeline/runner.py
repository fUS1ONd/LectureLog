from __future__ import annotations

import traceback
from pathlib import Path

from lecturelog.config import Settings
from lecturelog.llm.key_pool import KeyPool
from lecturelog.models import PipelineStage, PipelineStatus
from lecturelog.pipeline.audio_cut import cut_audio
from lecturelog.pipeline.export import export_result
from lecturelog.pipeline.slides import convert_slides
from lecturelog.pipeline.structurize import structurize
from lecturelog.pipeline.transcribe import transcribe


class PipelineRunner:
    def __init__(self, config: Settings, pool: KeyPool):
        self.config = config
        self.pool = pool
        self.statuses: dict[str, PipelineStatus] = {}

    def _set_status(
        self,
        task_id: str,
        *,
        stage: PipelineStage | None = None,
        progress_pct: int | None = None,
        error: str | None = None,
        result_path: str | None = None,
    ):
        current = self.statuses.get(task_id, PipelineStatus(task_id=task_id))
        update = current.model_copy(
            update={
                "stage": stage if stage is not None else current.stage,
                "progress_pct": progress_pct if progress_pct is not None else current.progress_pct,
                "error": error,
                "result_path": result_path if result_path is not None else current.result_path,
            }
        )
        self.statuses[task_id] = update

    async def run(self, task_id: str, audio_path: Path, slides_path: Path | None) -> Path:
        task_dir = Path(self.config.UPLOAD_DIR) / task_id
        task_dir.mkdir(parents=True, exist_ok=True)

        self._set_status(task_id, stage=PipelineStage.TRANSCRIBE, progress_pct=0, error=None)

        def transcribe_progress(pct: int):
            self._set_status(task_id, stage=PipelineStage.TRANSCRIBE, progress_pct=min(20, pct // 5))

        try:
            srt_path = await transcribe(
                audio_path=audio_path,
                output_dir=task_dir / "transcribe",
                groq_api_keys=self.config.groq_api_keys,
                on_progress=transcribe_progress,
            )

            slide_images: list[Path] = []
            if slides_path is not None:
                self._set_status(task_id, stage=PipelineStage.SLIDES, progress_pct=25)

                def slides_progress(pct: int):
                    self._set_status(task_id, stage=PipelineStage.SLIDES, progress_pct=20 + pct // 5)

                slide_images = await convert_slides(
                    path=slides_path,
                    output_dir=task_dir / "slides",
                    on_progress=slides_progress,
                )

            self._set_status(task_id, stage=PipelineStage.STRUCTURIZE, progress_pct=45)

            def structurize_progress(pct: int):
                self._set_status(task_id, stage=PipelineStage.STRUCTURIZE, progress_pct=45 + pct // 3)

            sections = await structurize(
                srt_path=srt_path,
                slide_images=slide_images,
                output_dir=task_dir / "structurize",
                pool=self.pool,
                models=self.config.gemini_models,
                on_progress=structurize_progress,
            )

            self._set_status(task_id, stage=PipelineStage.AUDIO_CUT, progress_pct=80)
            fragments = await cut_audio(
                audio_path=audio_path,
                sections=sections,
                output_dir=task_dir / "audio",
            )

            self._set_status(task_id, stage=PipelineStage.EXPORT, progress_pct=90)
            zip_path = await export_result(
                sections=sections,
                audio_fragments=fragments,
                slide_images=slide_images,
                output_dir=task_dir / "export",
            )

            self._set_status(
                task_id,
                stage=PipelineStage.EXPORT,
                progress_pct=100,
                error=None,
                result_path=str(zip_path),
            )
            return zip_path
        except Exception as exc:
            self._set_status(
                task_id,
                error=f"{exc}\n{traceback.format_exc()}",
                progress_pct=self.statuses.get(task_id, PipelineStatus(task_id=task_id)).progress_pct,
            )
            raise
