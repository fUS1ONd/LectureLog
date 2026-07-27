from __future__ import annotations

from typing import Literal

Confidence = Literal["verified", "probable", "unresolved"]


def confidence_from_margin(
    *,
    semantic_tier: str,
    margin: float,
    has_grounded_evidence: bool,
    has_competing_context: bool,
    visual_score: float | None = None,
) -> Confidence:
    if (
        semantic_tier == "explicit"
        and has_grounded_evidence
        and has_competing_context
        and margin >= 1.5
    ):
        return "verified"
    if (
        visual_score is not None
        and visual_score >= 0.85
        and has_competing_context
        and margin >= 1.0
    ):
        return "verified"
    if semantic_tier in {"explicit", "strong"} and has_grounded_evidence:
        return "probable"
    return "unresolved"
