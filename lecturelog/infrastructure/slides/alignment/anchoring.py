from __future__ import annotations

import logging

from lecturelog.domain.slides import SlideAssignment, SlideCatalogEntry, SlidePlacement
from lecturelog.infrastructure.slides.alignment.markers import (
    inject_marker,
    parse_markdown_blocks,
    strip_slide_markers,
)
from lecturelog.infrastructure.slides.alignment.retrieval import normalize_tokens

logger = logging.getLogger(__name__)


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
    # Existing markers are not semantic content and must not shift the stable
    # content-block index expected by inject_marker.
    blocks = parse_markdown_blocks(strip_slide_markers(markdown))
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
    try:
        anchored_markdown = inject_marker(
            markdown,
            slide_num=assignment.slide_num,
            block_index=block_index,
            side="after",
        )
    except Exception as error:  # noqa: BLE001 - anchoring is a post-render fail-safe boundary
        logger.warning("slide %d marker injection failed: %s", assignment.slide_num, error)
        return markdown, SlidePlacement(
            assignment.slide_num,
            "section_gallery",
            assignment.global_section_id,
            gallery_position="before_content",
            anchor_confidence="fallback",
            fallback_reason="anchor_injection_failed",
        )
    marker = f"<!-- slide:{assignment.slide_num} -->"
    if anchored_markdown.count(marker) != 1:
        logger.warning("slide %d marker verification failed", assignment.slide_num)
        return markdown, SlidePlacement(
            assignment.slide_num,
            "section_gallery",
            assignment.global_section_id,
            gallery_position="before_content",
            anchor_confidence="fallback",
            fallback_reason="anchor_injection_failed",
        )
    return (
        anchored_markdown,
        SlidePlacement(
            assignment.slide_num,
            "inline",
            assignment.global_section_id,
            block_index=block_index,
            side="after",
            anchor_confidence="verified",
        ),
    )
