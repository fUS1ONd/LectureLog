from __future__ import annotations

from dataclasses import dataclass

from lecturelog.domain.slides import SlideAssignment, SlideCandidate, SlideRelation
from lecturelog.infrastructure.slides.alignment.confidence import confidence_from_margin


@dataclass(frozen=True)
class AlignmentWeights:
    unmatched_score: float = 0.0
    backtrack_penalty: float = 1.5
    jump_penalty: float = 0.08
    explicit_bonus: float = 4.0
    strong_bonus: float = 2.0
    lexical_weight: float = 1.0
    visual_weight: float = 3.0
    progressive_same_section_bonus: float = 0.75


def align_sequence(
    slide_nums: list[int],
    candidates: dict[int, tuple[SlideCandidate, ...]],
    relations: tuple[SlideRelation, ...] = (),
    weights: AlignmentWeights = AlignmentWeights(),
) -> tuple[SlideAssignment, ...]:
    duplicate_of = {
        relation.slide_num: relation.canonical_slide_num
        for relation in relations
        if relation.kind == "exact_duplicate"
    }
    progressive_of = {
        relation.slide_num: relation.canonical_slide_num
        for relation in relations
        if relation.kind == "progressive_build"
    }
    best_total, best_path = _solve(
        slide_nums,
        candidates,
        duplicate_of,
        progressive_of,
        weights,
    )
    assignments: list[SlideAssignment] = []
    for index, (slide_num, chosen) in enumerate(zip(slide_nums, best_path, strict=True)):
        if slide_num in duplicate_of:
            assignments.append(
                SlideAssignment(
                    slide_num, "duplicate", None, (), None, "verified", 0.0,
                    f"duplicate_of:{duplicate_of[slide_num]}",
                )
            )
            continue
        if chosen is None:
            assignments.append(
                SlideAssignment(
                    slide_num, "unmentioned", None, (), None, "unresolved", 0.0,
                    "no_supported_evidence",
                )
            )
            continue
        constrained_total, _ = _solve(
            slide_nums,
            candidates,
            duplicate_of,
            progressive_of,
            weights,
            forbidden=(index, chosen.global_section_id),
        )
        margin = best_total - constrained_total
        best_score = _candidate_score(chosen, weights)
        confidence = confidence_from_margin(
            semantic_tier=chosen.semantic_tier,
            margin=margin,
            has_grounded_evidence=bool(chosen.evidence_block_ids and chosen.evidence_quote),
            visual_score=chosen.visual_score,
        )
        status = "discussed" if confidence != "unresolved" else "unmentioned"
        assignments.append(
            SlideAssignment(
                slide_num=slide_num,
                match_status=status,
                global_section_id=chosen.global_section_id if status == "discussed" else None,
                evidence_block_ids=chosen.evidence_block_ids if status == "discussed" else (),
                anchor_s=(chosen.anchor_start_s + chosen.anchor_end_s) / 2
                if status == "discussed"
                else None,
                assignment_confidence=confidence,
                score=best_score,
                reason_code=(
                    f"semantic_{chosen.semantic_tier}"
                    f":lexical={chosen.lexical_score:.3f}"
                    f":visual={(chosen.visual_score or 0.0):.3f}"
                    f":margin={margin:.3f}"
                ),
            )
        )
    return tuple(assignments)


def _solve(
    slide_nums: list[int],
    candidates: dict[int, tuple[SlideCandidate, ...]],
    duplicate_of: dict[int, int],
    progressive_of: dict[int, int],
    weights: AlignmentWeights,
    forbidden: tuple[int, int] | None = None,
) -> tuple[float, list[SlideCandidate | None]]:
    states: dict[int | None, tuple[float, list[SlideCandidate | None]]] = {
        None: (0.0, [])
    }
    for slide_index, slide_num in enumerate(slide_nums):
        if slide_num in duplicate_of:
            states = {
                previous: (score, path + [None])
                for previous, (score, path) in states.items()
            }
            continue
        options: tuple[SlideCandidate | None, ...] = (
            *(
                candidate
                for candidate in candidates.get(slide_num, ())
                if forbidden != (slide_index, candidate.global_section_id)
            ),
            None,
        )
        next_states: dict[int | None, tuple[float, list[SlideCandidate | None]]] = {}
        for previous_section, (base_score, path) in states.items():
            for option in options:
                section = option.global_section_id if option else previous_section
                score = base_score + _candidate_score(option, weights)
                if option is not None and previous_section is not None:
                    delta = option.global_section_id - previous_section
                    if delta < 0:
                        score -= weights.backtrack_penalty * abs(delta)
                    elif delta > 1:
                        score -= weights.jump_penalty * (delta - 1)
                    if (
                        slide_num in progressive_of
                        and option.global_section_id == previous_section
                    ):
                        score += weights.progressive_same_section_bonus
                existing = next_states.get(section)
                if existing is None or score > existing[0]:
                    next_states[section] = (score, path + [option])
        states = next_states
    return max(states.values(), key=lambda state: state[0])


def _candidate_score(candidate: SlideCandidate | None, weights: AlignmentWeights) -> float:
    if candidate is None:
        return weights.unmatched_score
    bonus = {
        "explicit": weights.explicit_bonus,
        "strong": weights.strong_bonus,
        "weak": 0.0,
        "none": -1.0,
    }[candidate.semantic_tier]
    return (
        candidate.lexical_score * weights.lexical_weight
        + bonus
        + (candidate.visual_score or 0.0) * weights.visual_weight
    )
