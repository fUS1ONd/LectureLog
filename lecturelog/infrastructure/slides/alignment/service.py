from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace
from pathlib import Path

from lecturelog.domain.slides import (
    SectionRef,
    SlideAsset,
    SlideAssignment,
    SlideCandidate,
    SlideCatalogEntry,
)
from lecturelog.infrastructure.llm.llm_client import LlmClient
from lecturelog.infrastructure.slides.alignment.catalog import (
    catalog_batches,
    detect_exact_duplicates,
    detect_progressive_builds,
    native_text_fallback,
    parse_catalog_response,
)
from lecturelog.infrastructure.slides.alignment.grounding import (
    evidence_matches_entry,
    evidence_specificity,
)
from lecturelog.infrastructure.slides.alignment.retrieval import generate_candidates
from lecturelog.infrastructure.slides.alignment.semantic import validate_semantic_response
from lecturelog.infrastructure.slides.alignment.sequence import AlignmentWeights, align_sequence
from lecturelog.infrastructure.srt import parse_srt_blocks, parse_srt_time

logger = logging.getLogger(__name__)
_NON_MATCHABLE_ROLES = {"blank"}


@dataclass(frozen=True)
class AlignmentTuning:
    candidate_limit: int = 5
    neighbor_radius: int = 1
    deck_min_supported_ratio: float = 0.08
    deck_min_supported_slides: int = 1
    weights: AlignmentWeights = AlignmentWeights()


class DocumentAlignmentService:
    """Evidence-grounded document alignment with fail-closed LLM enrichment."""

    def __init__(
        self,
        *,
        llm: LlmClient | None = None,
        models: list[str] | None = None,
        effort: str | None = None,
        prompts_dir: Path | None = None,
        tuning: AlignmentTuning = AlignmentTuning(),
    ) -> None:
        self._llm = llm
        self._models = models or []
        self._effort = effort
        self._prompts_dir = prompts_dir
        self._tuning = tuning

    async def align(
        self,
        *,
        assets: list[SlideAsset],
        section_layout: list[list[dict[str, object]]],
        srt_content: str,
        on_usage=None,
    ) -> tuple[SlideAssignment, ...]:
        blocks = parse_srt_blocks(srt_content)
        sections = self._section_refs(section_layout, blocks)
        entries, verified_catalog_slides = await self._catalog(assets, on_usage)
        candidates: dict[int, tuple[SlideCandidate, ...]] = {}
        for asset in assets:
            entry = entries.get(asset.slide_num)
            if entry is None or entry.role in _NON_MATCHABLE_ROLES:
                candidates[asset.slide_num] = ()
                continue
            retrieved = generate_candidates(
                entry,
                sections,
                blocks,
                limit=self._tuning.candidate_limit,
                neighbor_radius=self._tuning.neighbor_radius,
            )
            candidates[asset.slide_num] = await self._verify(
                entry,
                retrieved,
                blocks,
                sections,
                on_usage,
                catalog_verified=asset.slide_num in verified_catalog_slides,
            )

        relations = (
            *detect_exact_duplicates(assets),
            *detect_progressive_builds(assets),
        )
        assignments = align_sequence(
            [asset.slide_num for asset in assets],
            candidates,
            tuple(relations),
            self._tuning.weights,
        )
        assignments = self._decorate_roles(assignments, entries)
        assignments = self._downgrade_evidence_collisions(assignments, relations)
        supported = sum(item.match_status == "discussed" for item in assignments)
        content_count = sum(
            entry.role not in _NON_MATCHABLE_ROLES for entry in entries.values()
        )
        required = max(
            self._tuning.deck_min_supported_slides,
            int(content_count * self._tuning.deck_min_supported_ratio + 0.999),
        )
        if content_count and supported < required:
            return tuple(
                item
                if item.match_status == "duplicate"
                else SlideAssignment(
                    item.slide_num, "deck_mismatch", None, (), None, "unresolved",
                    item.score, "deck_guard_insufficient_grounded_coverage",
                )
                for item in assignments
            )
        return assignments

    async def _catalog(
        self, assets: list[SlideAsset], on_usage
    ) -> tuple[dict[int, SlideCatalogEntry], set[int]]:
        result: dict[int, SlideCatalogEntry] = {}
        verified: set[int] = set()
        for batch in catalog_batches(assets):
            parsed: list[SlideCatalogEntry] | None = None
            if self._llm is not None and self._models and all(
                _is_supported_image(asset.path) for asset in batch
            ):
                try:
                    prompt = self._prompt("document_slide_catalog_v1.md")
                    prompt += "\nslide_num в порядке изображений: " + json.dumps(
                        [asset.slide_num for asset in batch]
                    )
                    raw = await self._llm.call(
                        prompt=prompt,
                        models=self._models,
                        images=[asset.path.read_bytes() for asset in batch],
                        response_json=True,
                        effort=self._effort,
                        temperature=0,
                        on_usage=on_usage,
                    )
                    parsed = parse_catalog_response(
                        raw, [asset.slide_num for asset in batch]
                    )
                    verified.update(entry.slide_num for entry in parsed)
                except Exception as error:  # individual native-text fallback is safe
                    logger.warning("LLM slide catalog failed, native fallback: %s", error)
            for asset, entry in zip(batch, parsed or [None] * len(batch), strict=True):
                fallback = native_text_fallback(asset)
                selected = entry or fallback.entry
                if selected is not None:
                    result[asset.slide_num] = selected
        return result, verified

    async def _verify(
        self, entry, candidates, blocks, sections, on_usage, *, catalog_verified: bool
    ) -> tuple[SlideCandidate, ...]:
        if not candidates:
            return ()
        if (
            self._llm is None
            or not self._models
            or not catalog_verified
        ):
            grounded = self._lexical_ground(entry, candidates, blocks)
            return (grounded,) if grounded else ()
        payload = {
            "slide": {
                "slide_num": entry.slide_num,
                "title": entry.title,
                "visible_text": entry.visible_text,
                "source_concepts": entry.source_concepts,
                "transcript_language_terms": entry.transcript_language_terms,
                "formulas": entry.formulas,
            },
            "candidates": [
                {
                    "global_section_id": candidate.global_section_id,
                    "evidence_blocks": [
                        {"block_id": block.block_id, "text": block.text}
                        for block in blocks
                        if block.block_id in candidate.evidence_block_ids
                    ],
                }
                for candidate in candidates
            ],
        }
        prompt = self._prompt("document_slide_semantic_match_v1.md")
        prompt += "\n" + json.dumps(payload, ensure_ascii=False)
        try:
            raw = await self._llm.call(
                prompt=prompt, models=self._models, response_json=True,
                effort=self._effort, temperature=0, on_usage=on_usage,
            )
            first = validate_semantic_response(
                raw, entry=entry, candidates=candidates, blocks=blocks
            )
            # Strong evidence is accepted only after an independent second pass.
            if first is None:
                response = json.loads(raw)
                if response.get("semantic_tier") != "strong":
                    return self._global_recovery(entry, sections, blocks)
                second_raw = await self._llm.call(
                    prompt=prompt + "\nНезависимо перепроверь strong verdict.",
                    models=self._models, response_json=True,
                    effort=self._effort, temperature=0, on_usage=on_usage,
                )
                second = validate_semantic_response(
                    second_raw, entry=entry, candidates=candidates, blocks=blocks,
                    strong_judge_agrees=True,
                )
                if second and second.semantic_tier == "strong":
                    return (self._with_competition(second, candidates),)
                return self._global_recovery(entry, sections, blocks)
            return (self._with_competition(first, candidates),)
        except Exception as error:
            logger.warning(
                "semantic verification failed closed for slide %d: %s",
                entry.slide_num,
                error,
            )
            return self._global_recovery(entry, sections, blocks)

    @staticmethod
    def _lexical_ground(entry, candidates, blocks) -> SlideCandidate | None:
        # Backwards-compatible, deterministic path for tests/offline operation.
        by_id = {block.block_id: block for block in blocks}
        matches = []
        for candidate in candidates:
            if candidate.lexical_score <= 1.0:
                continue
            for block_id in candidate.evidence_block_ids:
                block = by_id[block_id]
                if evidence_matches_entry(block.text, entry):
                    matches.append((
                        evidence_specificity(block.text, entry),
                        candidate.lexical_score,
                        -block.start_s,
                        candidate,
                        block,
                    ))
        if matches:
            _, _, _, candidate, block = max(matches, key=lambda item: item[:3])
            grounded = SlideCandidate(
                candidate.slide_num, candidate.global_section_id, (block.block_id,),
                block.text, block.start_s, block.end_s,
                candidate.lexical_score, "explicit", candidate.visual_score,
            )
            return DocumentAlignmentService._with_competition(grounded, candidates)
        return None

    @staticmethod
    def _with_competition(candidate, candidates):
        alternatives = [
            item.lexical_score
            for item in candidates
            if item.global_section_id != candidate.global_section_id
        ]
        margin = (
            candidate.lexical_score - max(alternatives)
            if alternatives
            else None
        )
        return replace(candidate, competition_margin=margin)

    def _global_recovery(self, entry, sections, blocks):
        recovered_pool = generate_candidates(
            entry,
            sections,
            blocks,
            limit=len(sections),
            neighbor_radius=0,
        )
        recovered = self._lexical_ground(entry, recovered_pool, blocks)
        if recovered is None:
            return ()
        return (replace(recovered, semantic_tier="strong"),)

    @staticmethod
    def _decorate_roles(assignments, entries):
        result = []
        for item in assignments:
            entry = entries.get(item.slide_num)
            if entry and entry.role == "blank" and item.match_status != "duplicate":
                result.append(SlideAssignment(
                    item.slide_num, "unmentioned", None, (), None, "unresolved",
                    item.score, f"service_role:{entry.role}",
                ))
            else:
                result.append(item)
        return tuple(result)

    @staticmethod
    def _downgrade_evidence_collisions(assignments, relations):
        progressive = {
            slide_num
            for relation in relations
            if relation.kind == "progressive_build"
            for slide_num in (relation.slide_num, relation.canonical_slide_num)
        }
        by_evidence: dict[int, list[int]] = {}
        for item in assignments:
            if item.match_status != "discussed":
                continue
            for block_id in item.evidence_block_ids:
                if item.slide_num not in progressive:
                    by_evidence.setdefault(block_id, []).append(item.slide_num)
        conflicted = {
            slide_num
            for slide_nums in by_evidence.values()
            if len(slide_nums) > 2
            for slide_num in slide_nums
        }
        return tuple(
            replace(
                item,
                assignment_confidence="probable",
                reason_code=f"{item.reason_code}:evidence_collision",
            )
            if item.slide_num in conflicted
            and item.assignment_confidence == "verified"
            else item
            for item in assignments
        )

    def _prompt(self, name: str) -> str:
        if self._prompts_dir is None:
            raise RuntimeError("prompts_dir is required for LLM alignment")
        return (self._prompts_dir / name).read_text(encoding="utf-8")

    @staticmethod
    def _section_refs(section_layout, blocks) -> tuple[SectionRef, ...]:
        refs: list[SectionRef] = []
        previous_end = -1.0
        transcript_end = max((block.end_s for block in blocks), default=0.0)
        for topic_index, sections in enumerate(section_layout):
            for local_index, section in enumerate(sections):
                start = parse_srt_time(str(section["start"]))
                end = parse_srt_time(str(section["end"]))
                if start < 0 or end <= start:
                    raise ValueError("invalid section timeline")
                # Subsplit responses commonly overlap adjacent boundaries by a
                # few seconds. Repair an advancing range instead of throwing
                # away alignment for the whole deck. A range that ends before
                # the previous section remains genuinely non-monotonic.
                if start < previous_end:
                    if end <= previous_end:
                        raise ValueError("non-progressing section timeline")
                    start = previous_end
                if transcript_end and end > transcript_end + 5.0:
                    raise ValueError("section timeline exceeds transcript")
                refs.append(SectionRef(
                    len(refs), topic_index, local_index, start, end
                ))
                previous_end = end
        if not refs:
            raise ValueError("section timeline is empty")
        return tuple(refs)


def _is_supported_image(path: Path) -> bool:
    prefix = path.read_bytes()[:12]
    return prefix.startswith(b"\x89PNG") or prefix.startswith(b"\xff\xd8")
