from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    MAJOR = "major"
    CRITICAL = "critical"


BlockKind = Literal[
    "heading",
    "paragraph",
    "list",
    "quote",
    "code",
    "table",
    "image",
    "metadata",
]


@dataclass(frozen=True)
class Finding:
    code: str
    severity: Severity
    message: str
    artifact: str | None = None
    block_id: int | None = None
    section_id: int | None = None
    slide_num: int | None = None
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class LanguageAnalysis:
    detected: str | None
    confidence: float
    cyrillic_letters: int
    latin_letters: int
    natural_words: int
    is_mixed: bool = False
    ignored: bool = False
    reason: str | None = None


@dataclass(frozen=True)
class NoteBlock:
    block_id: int
    kind: BlockKind
    text: str
    heading_path: tuple[str, ...]
    line_start: int
    line_end: int
    section_id: int | None = None
    language: LanguageAnalysis | None = None


@dataclass(frozen=True)
class TranscriptCue:
    block_id: int
    start_s: float
    end_s: float
    text: str


@dataclass(frozen=True)
class SectionArtifact:
    section_id: int
    topic_title: str
    title: str
    content_md: str
    start_s: float | None
    end_s: float | None
    slide_nums: tuple[int, ...] = ()


@dataclass(frozen=True)
class SlideArtifact:
    slide_num: int
    path: str | None
    native_text: str = ""
    native_text_quality: Literal["good", "sparse", "none"] = "none"
    image_data_url: str | None = None


@dataclass(frozen=True)
class AlignmentData:
    schema_version: int | None
    mode: str | None
    assignments: tuple[dict[str, Any], ...] = ()
    placements: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class EvaluationArtifacts:
    source_zip: Path
    note_markdown: str
    structure: dict[str, Any] | None
    blocks: tuple[NoteBlock, ...]
    sections: tuple[SectionArtifact, ...]
    transcript: tuple[TranscriptCue, ...]
    slides: tuple[SlideArtifact, ...]
    alignment: AlignmentData | None
    members: tuple[str, ...]
    pdf_page_count: int | None = None
    load_findings: tuple[Finding, ...] = field(default_factory=tuple)
