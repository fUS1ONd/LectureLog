from __future__ import annotations

from lecturelog.domain.slides import (
    SectionRef,
    SlideAsset,
    SlideAssignment,
    SlideCandidate,
    SlideCatalogEntry,
)
from lecturelog.infrastructure.slides.alignment.catalog import (
    detect_exact_duplicates,
    native_text_fallback,
)
from lecturelog.infrastructure.slides.alignment.retrieval import (
    generate_candidates,
    normalize_tokens,
)
from lecturelog.infrastructure.slides.alignment.sequence import align_sequence
from lecturelog.infrastructure.srt import parse_srt_blocks, parse_srt_time


class DocumentAlignmentService:
    """Deterministic evidence boundary used by shadow/v2.

    VLM catalog and semantic judge can enrich the same contracts later; native
    text never becomes an assignment unless an exact transcript token grounds it.
    """

    def align(
        self,
        *,
        assets: list[SlideAsset],
        section_layout: list[list[dict[str, object]]],
        srt_content: str,
    ) -> tuple[SlideAssignment, ...]:
        blocks = parse_srt_blocks(srt_content)
        sections = self._section_refs(section_layout)
        candidates: dict[int, tuple[SlideCandidate, ...]] = {}
        for asset in assets:
            catalog = native_text_fallback(asset)
            if catalog.entry is None:
                candidates[asset.slide_num] = ()
                continue
            retrieved = generate_candidates(catalog.entry, sections, blocks)
            candidates[asset.slide_num] = tuple(
                supported
                for candidate in retrieved
                if (supported := self._ground(candidate, catalog.entry, blocks)) is not None
            )
        return align_sequence(
            [asset.slide_num for asset in assets],
            candidates,
            detect_exact_duplicates(assets),
        )

    @staticmethod
    def _section_refs(
        section_layout: list[list[dict[str, object]]],
    ) -> tuple[SectionRef, ...]:
        refs: list[SectionRef] = []
        for topic_index, sections in enumerate(section_layout):
            for local_index, section in enumerate(sections):
                refs.append(
                    SectionRef(
                        global_section_id=len(refs),
                        topic_index=topic_index,
                        local_section_index=local_index,
                        start_s=parse_srt_time(str(section["start"])),
                        end_s=parse_srt_time(str(section["end"])),
                    )
                )
        return tuple(refs)

    @staticmethod
    def _ground(
        candidate: SlideCandidate,
        entry: SlideCatalogEntry,
        blocks,
    ) -> SlideCandidate | None:
        if candidate.lexical_score <= 1.0:
            return None
        slide_tokens = set(
            normalize_tokens(
                " ".join([entry.title or "", entry.visible_text, *entry.source_concepts])
            )
        )
        by_id = {block.block_id: block for block in blocks}
        for block_id in candidate.evidence_block_ids:
            block = by_id[block_id]
            overlap = slide_tokens & set(normalize_tokens(block.text))
            if overlap:
                return SlideCandidate(
                    slide_num=candidate.slide_num,
                    global_section_id=candidate.global_section_id,
                    evidence_block_ids=(block_id,),
                    evidence_quote=block.text,
                    anchor_start_s=block.start_s,
                    anchor_end_s=block.end_s,
                    lexical_score=candidate.lexical_score,
                    semantic_tier="explicit",
                    visual_score=candidate.visual_score,
                )
        return None

