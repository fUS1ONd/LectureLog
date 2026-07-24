from __future__ import annotations

from lecturelog.domain.slides import SlideAssignment, SlideCatalogEntry, SlidePlacement
from lecturelog.infrastructure.slides.alignment.markers import inject_marker, parse_markdown_blocks
from lecturelog.infrastructure.slides.alignment.retrieval import normalize_tokens


def anchor_assignment(
    assignment: SlideAssignment,
    entry: SlideCatalogEntry | None,
    markdown: str,
) -> tuple[str, SlidePlacement]:
    if assignment.match_status != "discussed" or assignment.global_section_id is None:
        return markdown, SlidePlacement(
            assignment.slide_num,
            "appendix",
            None,
            anchor_confidence="none",
            fallback_reason=assignment.reason_code,
        )
    if assignment.assignment_confidence != "verified" or entry is None:
        return markdown, SlidePlacement(
            assignment.slide_num,
            "section_gallery",
            assignment.global_section_id,
            gallery_position="before_content",
            anchor_confidence="probable",
            fallback_reason="assignment_not_verified",
        )
    blocks = parse_markdown_blocks(markdown)
    query = set(normalize_tokens(" ".join([entry.title or "", entry.visible_text])))
    ranked = [
        (len(query & set(normalize_tokens(block.text))), index)
        for index, block in enumerate(blocks)
        if not block.atomic
    ]
    if not ranked or max(ranked)[0] == 0:
        return markdown, SlidePlacement(
            assignment.slide_num,
            "section_gallery",
            assignment.global_section_id,
            gallery_position="before_content",
            anchor_confidence="fallback",
            fallback_reason="no_safe_semantic_block",
        )
    _, block_index = max(ranked)
    return (
        inject_marker(
            markdown,
            slide_num=assignment.slide_num,
            block_index=block_index,
            side="after",
        ),
        SlidePlacement(
            assignment.slide_num,
            "inline",
            assignment.global_section_id,
            block_index=block_index,
            side="after",
            anchor_confidence="verified",
        ),
    )

