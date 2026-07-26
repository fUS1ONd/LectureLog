from __future__ import annotations

from lecturelog.domain.slides import SlideCandidate, SlideCatalogEntry, TranscriptBlock
from lecturelog.infrastructure.slides.alignment.grounding import evidence_matches_entry
from lecturelog.infrastructure.slides.alignment.schemas import SemanticMatchResponse


def validate_semantic_response(
    raw: str,
    *,
    entry: SlideCatalogEntry,
    candidates: tuple[SlideCandidate, ...],
    blocks: list[TranscriptBlock],
    strong_judge_agrees: bool = False,
) -> SlideCandidate | None:
    response = SemanticMatchResponse.model_validate_json(raw)
    if response.slide_num != entry.slide_num:
        raise ValueError("semantic response ссылается на другой slide_num")
    candidate = next(
        (item for item in candidates if item.global_section_id == response.global_section_id),
        None,
    )
    if candidate is None:
        raise ValueError("semantic response ссылается на section вне candidate pool")
    allowed_ids = set(candidate.evidence_block_ids)
    if not set(response.evidence_block_ids).issubset(allowed_ids):
        raise ValueError("semantic response содержит недоступные evidence block IDs")
    by_id = {block.block_id: block for block in blocks}
    evidence_text = " ".join(by_id[block_id].text for block_id in response.evidence_block_ids)
    quote = (response.evidence_quote or "").strip()
    if quote and " ".join(quote.casefold().split()) not in " ".join(
        evidence_text.casefold().split()
    ):
        raise ValueError("evidence quote отсутствует в указанных SRT blocks")
    if response.semantic_tier == "explicit" and not evidence_matches_entry(quote, entry):
        raise ValueError("explicit quote не подтверждает термин/утверждение слайда")
    if response.semantic_tier == "strong" and not strong_judge_agrees:
        return None
    if response.semantic_tier in {"weak", "none"}:
        return None
    evidence = [by_id[block_id] for block_id in response.evidence_block_ids]
    return SlideCandidate(
        slide_num=candidate.slide_num,
        global_section_id=candidate.global_section_id,
        evidence_block_ids=tuple(response.evidence_block_ids),
        evidence_quote=quote or None,
        anchor_start_s=evidence[0].start_s if evidence else candidate.anchor_start_s,
        anchor_end_s=evidence[-1].end_s if evidence else candidate.anchor_end_s,
        lexical_score=candidate.lexical_score,
        semantic_tier=response.semantic_tier,
        visual_score=candidate.visual_score,
    )
