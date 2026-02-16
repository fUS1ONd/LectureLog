from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class PipelineStage(StrEnum):
    TRANSCRIBE = "transcribe"
    SLIDES = "slides"
    STRUCTURIZE = "structurize"
    AUDIO_CUT = "audio_cut"
    EXPORT = "export"


class PipelineStatus(BaseModel):
    task_id: str
    stage: PipelineStage | None = None
    progress_pct: int = 0
    error: str | None = None
    result_path: str | None = None


class Section(BaseModel):
    title: str
    start: str
    end: str
    content: str
    slide_indices: list[int]

