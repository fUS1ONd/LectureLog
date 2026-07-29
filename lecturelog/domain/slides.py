from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from lecturelog.domain.models import Topic

SlideOrigin = Literal["document", "video"]
NativeTextQuality = Literal["good", "sparse", "none"]


@dataclass(frozen=True)
class SlideAsset:
    slide_num: int
    path: Path
    origin: SlideOrigin
    timestamp: float | None = None
    caption: str | None = None
    extracted_text: str | None = None
    native_text_quality: NativeTextQuality | None = None

    def __post_init__(self) -> None:
        if self.slide_num < 1:
            raise ValueError("slide_num должен быть положительным 1-based номером")
        if self.origin == "document":
            if self.timestamp is not None:
                raise ValueError("document slide не может иметь timestamp")
            if self.native_text_quality is None or self.extracted_text is None:
                raise ValueError("document slide требует extracted_text и native_text_quality")
        elif self.origin == "video":
            if self.timestamp is None:
                raise ValueError("video slide требует timestamp")
            if self.native_text_quality is not None or self.extracted_text is not None:
                raise ValueError("video slide не может иметь native text metadata")
        else:
            raise ValueError(f"Неизвестный origin слайда: {self.origin}")


@dataclass(frozen=True)
class SlideCatalogEntry:
    slide_num: int
    role: Literal["content", "title", "agenda", "section_divider", "closing", "appendix", "blank"]
    title: str | None
    visible_text: str
    source_concepts: tuple[str, ...] = ()
    transcript_language_terms: tuple[str, ...] = ()
    visual_summary: str = ""
    formulas: tuple[str, ...] = ()
    proper_nouns: tuple[str, ...] = ()


@dataclass(frozen=True)
class SlideCatalogResult:
    slide_num: int
    status: Literal["verified", "native_text_fallback", "unresolved"]
    entry: SlideCatalogEntry | None

    def __post_init__(self) -> None:
        if (self.status == "unresolved") != (self.entry is None):
            raise ValueError("unresolved требует entry=None, остальные статусы требуют entry")
        if self.entry is not None and self.entry.slide_num != self.slide_num:
            raise ValueError("slide_num результата и entry не совпадают")


@dataclass(frozen=True)
class SlideRelation:
    slide_num: int
    kind: Literal["exact_duplicate", "progressive_build"]
    group_id: str
    canonical_slide_num: int


@dataclass(frozen=True)
class TranscriptBlock:
    block_id: int
    start_s: float
    end_s: float
    text: str


@dataclass(frozen=True)
class SectionRef:
    global_section_id: int
    topic_index: int
    local_section_index: int
    start_s: float
    end_s: float


@dataclass(frozen=True)
class SlideCandidate:
    slide_num: int
    global_section_id: int
    evidence_block_ids: tuple[int, ...]
    evidence_quote: str | None
    anchor_start_s: float
    anchor_end_s: float
    lexical_score: float
    semantic_tier: Literal["explicit", "strong", "weak", "none"] = "none"
    visual_score: float | None = None
    competition_margin: float | None = None


@dataclass(frozen=True)
class SlideAssignment:
    slide_num: int
    match_status: Literal["discussed", "unmentioned", "duplicate", "deck_mismatch"]
    global_section_id: int | None
    evidence_block_ids: tuple[int, ...]
    anchor_s: float | None
    assignment_confidence: Literal["verified", "probable", "unresolved"]
    score: float
    reason_code: str


@dataclass(frozen=True)
class SlidePlacement:
    slide_num: int
    output_kind: Literal["inline", "section_gallery", "appendix", "suppressed"]
    global_section_id: int | None
    block_index: int | None = None
    side: Literal["before", "after"] | None = None
    gallery_position: Literal["before_content", "after_content"] | None = None
    anchor_confidence: Literal["verified", "probable", "fallback", "none"] = "none"
    fallback_reason: str | None = None


@dataclass(frozen=True)
class StructurizeContext:
    source_kind: Literal["audio", "video"]
    local_video_path: Path | None = None


@dataclass(frozen=True)
class StructurizeResult:
    topics: list[Topic]
    slide_assignments: tuple[SlideAssignment, ...] = ()
    slide_placements: tuple[SlidePlacement, ...] = ()

    def __iter__(self):
        return iter(self.topics)

    def __len__(self) -> int:
        return len(self.topics)

    def __getitem__(self, index: int) -> Topic:
        return self.topics[index]
