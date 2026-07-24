from __future__ import annotations

from typing import Literal

Confidence = Literal["verified", "probable", "unresolved"]


def confidence_from_margin(
    *,
    semantic_tier: str,
    margin: float,
    has_grounded_evidence: bool,
    visual_score: float | None = None,
) -> Confidence:
    if semantic_tier == "explicit" and has_grounded_evidence and margin >= 1.5:
        return "verified"
    if visual_score is not None and visual_score >= 0.85 and margin >= 1.0:
        return "verified"
    if semantic_tier in {"explicit", "strong"} and margin >= 0.35:
        return "probable"
    return "unresolved"

