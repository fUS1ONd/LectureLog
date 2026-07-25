"""Local, deterministic aggregation of evaluator observations."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, is_dataclass
from typing import Any

DIMENSION_WEIGHTS = {
    "faithfulness": 0.20,
    "content_coverage": 0.12,
    "block_quality": 0.18,
    "document_structure": 0.10,
    "slide_semantic_relevance": 0.17,
    "slide_anchor_precision": 0.13,
    "confidence_calibration": 0.10,
}

BLOCK_WEIGHTS = {
    "language_consistency": 0.25,
    "clarity": 0.20,
    "local_coherence": 0.20,
    "heading_relevance": 0.15,
    "information_value": 0.10,
    "style_consistency": 0.05,
    "formatting": 0.05,
}

SEVERITY_RANK = {"critical": 4, "major": 3, "warning": 2, "minor": 2, "info": 1}
BROKEN_INVARIANT_CODES = {
    "unsafe_zip_path",
    "broken_artifact_input",
    "broken_markdown_reference",
    "unclosed_markdown_fence",
    "pdf_exported_slide_count_mismatch",
    "placement_section_out_of_range",
}


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return min(100.0, max(0.0, float(value)))


def _mean(values: Iterable[Any]) -> float | None:
    numbers = [number for value in values if (number := _number(value)) is not None]
    return sum(numbers) / len(numbers) if numbers else None


def _objects(items: Iterable[Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, Mapping):
            result.append(dict(item))
        elif hasattr(item, "model_dump"):
            result.append(item.model_dump(mode="json"))
        elif is_dataclass(item) and not isinstance(item, type):
            result.append(asdict(item))
    return result


def _dimension_score(
    explicit: Mapping[str, Any],
    key: str,
    blocks: list[dict[str, Any]],
    sections: list[dict[str, Any]],
    slides: list[dict[str, Any]],
) -> float | None:
    if (score := _number(explicit.get(key))) is not None:
        return score
    source: list[dict[str, Any]]
    aliases: tuple[str, ...]
    if key == "faithfulness":
        source, aliases = blocks, ("faithfulness",)
    elif key == "content_coverage":
        source, aliases = sections, ("content_coverage", "coverage")
    elif key == "block_quality":
        components: dict[str, float] = {}
        for component in BLOCK_WEIGHTS:
            if (value := _mean(block.get(component) for block in blocks)) is not None:
                components[component] = value
        weight = sum(BLOCK_WEIGHTS[name] for name in components)
        return (
            sum(components[name] * BLOCK_WEIGHTS[name] for name in components) / weight
            if weight
            else None
        )
    elif key == "document_structure":
        source, aliases = sections, ("document_structure", "structure")
    elif key == "slide_semantic_relevance":
        combined: list[float] = []
        for slide in slides:
            relevance = _number(
                slide.get("slide_semantic_relevance", slide.get("semantic_relevance"))
            )
            specificity = _number(slide.get("specificity"))
            if relevance is not None and specificity is not None:
                combined.append(relevance * 0.6 + specificity * 0.4)
            elif relevance is not None:
                combined.append(relevance)
            elif specificity is not None:
                combined.append(specificity)
        return _mean(combined)
    elif key == "slide_anchor_precision":
        source, aliases = slides, ("slide_anchor_precision", "anchor_precision")
    else:
        source, aliases = slides, ("confidence_calibration", "calibration")
    return _mean(item.get(alias) for item in source for alias in aliases)


def _finding_code(finding: Mapping[str, Any]) -> str:
    return str(finding.get("code") or finding.get("issue_code") or "unknown")


def _typed_critical(findings: Iterable[Mapping[str, Any]], kinds: set[str]) -> int:
    return sum(
        str(finding.get("kind", "")).lower() in kinds
        and str(finding.get("severity", "")).lower() == "critical"
        for finding in findings
    )


def _gate(
    gate_id: str,
    label: str,
    actual: int | float | None,
    operator: str,
    threshold: int | float,
    *,
    provisional: bool,
) -> dict[str, Any]:
    if actual is None:
        status = "unknown"
    elif operator == "<=":
        status = "pass" if actual <= threshold else "fail"
    elif operator == ">=":
        status = "pass" if actual >= threshold else "fail"
    else:
        status = "pass" if actual == threshold else "fail"
    return {
        "id": gate_id,
        "label": label,
        "status": status,
        "actual": actual,
        "operator": operator,
        "threshold": threshold,
        "provisional": provisional,
    }


def _ratio(count: int, total: int) -> float:
    return round(100 * count / total, 2) if total else 0.0


def _count(
    coverage: Mapping[str, Any],
    key: str,
    explicit: int | None,
    fallback: int,
) -> int:
    value = coverage.get(key, explicit)
    valid = isinstance(value, int) and not isinstance(value, bool) and value >= 0
    return value if valid else fallback


def _is_full_coverage(
    coverage: Mapping[str, Any], kind: str, evaluated: int, total: int
) -> bool:
    explicit = coverage.get(f"{kind}_complete")
    if isinstance(explicit, bool):
        return explicit and evaluated >= total
    return total > 0 and evaluated >= total


def _coverage_record(evaluated: int, total: int, complete: bool) -> dict[str, Any]:
    return {
        "evaluated": evaluated,
        "total": total,
        "percent": _ratio(evaluated, total) if total else None,
        "complete": complete,
    }


def _deduplicate_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for finding in findings:
        identity = (
            _finding_code(finding),
            finding.get("stable_id"),
            finding.get("block_id"),
            finding.get("section_id"),
            finding.get("slide_num"),
            finding.get("message") or finding.get("detail"),
        )
        if identity not in seen:
            seen.add(identity)
            result.append(finding)
    return result


def _verdict(
    score: float | None,
    gates: list[dict[str, Any]],
    complete: bool,
    *,
    profile: str,
    judge_stability: float | None,
) -> str:
    failed = {gate["id"] for gate in gates if gate["status"] == "fail"}
    if "broken_output_invariant" in failed:
        return "invalid"
    if not complete:
        return "evaluation_inconclusive"
    if profile == "static":
        return "evaluation_inconclusive"
    if profile == "smoke":
        return "sampled_directional"
    if score is None:
        return "evaluation_inconclusive"
    if failed & {"unexpected_full_language_blocks", "critical_incomplete_blocks"}:
        return "usable_with_major_consistency_issues" if score >= 45 else "poor"
    if failed & {
        "incorrect_slide_placements",
        "verified_incorrect_slide_placements",
        "slide_anchor_quality",
        "deterministic_alignment_anomalies",
    }:
        return "usable_with_alignment_issues" if score >= 45 else "poor"
    if failed:
        return "poor" if score < 60 else "usable_with_minor_issues"
    # A single free judge without repeated stability evidence cannot certify a release.
    if judge_stability is None and score >= 65:
        return "usable_with_minor_issues"
    if score >= 90:
        return "excellent"
    if score >= 80:
        return "good"
    if score >= 65:
        return "usable_with_minor_issues"
    return "poor"


def aggregate_evaluation(
    *,
    deterministic_findings: Iterable[Any] = (),
    judge_findings: Iterable[Any] = (),
    block_evaluations: Iterable[Any] = (),
    section_evaluations: Iterable[Any] = (),
    slide_evaluations: Iterable[Any] = (),
    dimension_scores: Mapping[str, Any] | None = None,
    profile: str = "static",
    incomplete_reasons: Iterable[str] = (),
    judge_stability: float | None = None,
    usage: Mapping[str, Any] | None = None,
    blocks_inspected: int | None = None,
    sections_inspected: int | None = None,
    slides_inspected: int | None = None,
    coverage: Mapping[str, Any] | None = None,
    stability: Mapping[str, Any] | None = None,
    release_capable: bool = True,
) -> dict[str, Any]:
    """Build the canonical evaluation summary from local and judge observations."""
    deterministic = _objects(deterministic_findings)
    judged = _objects(judge_findings)
    findings = _deduplicate_findings([*deterministic, *judged])
    blocks = _objects(block_evaluations)
    sections = _objects(section_evaluations)
    slides = _objects(slide_evaluations)
    explicit = dimension_scores or {}
    scores = {
        key: _dimension_score(explicit, key, blocks, sections, slides)
        for key in DIMENSION_WEIGHTS
    }
    available_weight = sum(
        DIMENSION_WEIGHTS[key] for key, value in scores.items() if value is not None
    )
    overall = (
        sum(scores[key] * DIMENSION_WEIGHTS[key] for key in scores if scores[key] is not None)
        / available_weight
        if available_weight
        else None
    )
    overall = round(overall, 1) if overall is not None else None

    codes = [_finding_code(finding) for finding in findings]
    # Remote codes are model-authored labels. Blocking semantic gates use the typed
    # rubric dimension plus severity, so inventing/omitting a magic code cannot bypass them.
    typed_contradictions = _typed_critical(judged, {"faithfulness"})
    typed_unsupported = _typed_critical(
        judged, {"insufficient_evidence", "content_coverage"}
    )
    coverage_data = dict(coverage or {})
    block_total = _count(coverage_data, "blocks_total", blocks_inspected, len(blocks))
    section_total = _count(coverage_data, "sections_total", sections_inspected, len(sections))
    slide_total = _count(coverage_data, "slides_total", slides_inspected, len(slides))
    block_complete = _is_full_coverage(coverage_data, "blocks", len(blocks), block_total)
    slide_complete = _is_full_coverage(coverage_data, "slides", len(slides), slide_total)
    language_errors = codes.count("unexpected_full_language_block") + sum(
        block.get("language_verdict") == "unexpected_language" for block in blocks
    )
    incomplete_blocks = codes.count("critical_incomplete_block")
    def placement_issue(slide: Mapping[str, Any]) -> bool:
        if str(slide.get("placement_verdict", "")).lower() in {"incorrect", "weak"}:
            return True
        rank = slide.get("current_context_rank")
        better_context = slide.get("better_context_id")
        confidence = slide.get(
            "better_context_confidence",
            slide.get("confidence", 0),
        )
        return (
            isinstance(rank, int | float)
            and not isinstance(rank, bool)
            and rank > 1
            and bool(better_context)
            and isinstance(confidence, int | float)
            and not isinstance(confidence, bool)
            and confidence >= 0.70
        )

    incorrect_slides = sum(placement_issue(slide) for slide in slides)
    verified_incorrect = sum(
        placement_issue(slide)
        and str(slide.get("system_source_confidence", "")).lower() == "verified"
        for slide in slides
    )
    alignment_anomalies = sum(
        _finding_code(finding) in {"slide_anchor_collapse", "slide_section_concentration"}
        and str(finding.get("severity", "")).lower() in {"major", "critical"}
        for finding in deterministic
    )
    effective_stability = judge_stability
    if effective_stability is None and isinstance(stability, Mapping):
        candidate = stability.get("score", stability.get("judge_stability"))
        if isinstance(candidate, int | float) and not isinstance(candidate, bool):
            effective_stability = float(candidate)
    provisional = True
    gates = [
        _gate(
            "broken_output_invariant",
            "Broken output invariants",
            sum(
                code.startswith("broken_") or code in BROKEN_INVARIANT_CODES for code in codes
            ),
            "==",
            0,
            provisional=provisional,
        ),
        _gate(
            "critical_transcript_contradiction",
            "Critical transcript contradictions",
            typed_contradictions,
            "==",
            0,
            provisional=provisional,
        ),
        _gate(
            "unsupported_critical_claim",
            "Unsupported critical claims",
            typed_unsupported,
            "<=",
            1,
            provisional=provisional,
        ),
        _gate(
            "unexpected_full_language_blocks",
            "Unexpected full-language prose blocks",
            _ratio(language_errors, block_total)
            if block_total and (block_complete or not blocks)
            else None,
            "<=",
            1,
            provisional=provisional,
        ),
        _gate(
            "critical_incomplete_blocks",
            "Critical incomplete blocks",
            incomplete_blocks,
            "==",
            0,
            provisional=provisional,
        ),
        _gate(
            "incorrect_slide_placements",
            "Incorrect slide placements",
            _ratio(incorrect_slides, slide_total) if slide_total and slide_complete else None,
            "<=",
            10,
            provisional=provisional,
        ),
        _gate(
            "verified_incorrect_slide_placements",
            "Verified but incorrect slide placements",
            _ratio(verified_incorrect, slide_total)
            if slide_total and slide_complete
            else None,
            "<=",
            3,
            provisional=provisional,
        ),
        _gate(
            "slide_anchor_quality",
            "Slide anchor quality score",
            scores["slide_anchor_precision"],
            ">=",
            60,
            provisional=provisional,
        ),
        _gate(
            "deterministic_alignment_anomalies",
            "Major deterministic alignment anomalies",
            alignment_anomalies,
            "==",
            0,
            provisional=provisional,
        ),
        _gate(
            "judge_stability",
            "Judge stability",
            effective_stability,
            ">=",
            0.80,
            provisional=provisional,
        ),
    ]

    reasons = list(incomplete_reasons)
    if profile != "static" and any(value is None for value in scores.values()):
        reasons.append("not all requested judge dimensions were evaluated")
    dimensions_complete = all(value is not None for value in scores.values())
    complete = not reasons and (profile == "static" or dimensions_complete)
    ranked_findings = sorted(
        findings,
        key=lambda item: (
            -SEVERITY_RANK.get(str(item.get("severity", "info")).lower(), 0),
            str(item.get("code", "")),
        ),
    )
    return {
        "schema_version": "1",
        "profile": profile,
        "status": "complete" if complete else "incomplete",
        "incomplete_reasons": reasons,
        "verdict": _verdict(
            overall,
            gates,
            complete,
            profile=profile,
            judge_stability=effective_stability if release_capable else None,
        ),
        "overall_score": overall,
        "scorecard": scores,
        "available_score_weight": round(available_weight, 2),
        "quality_gates": gates,
        "critical_error_count": sum(
            str(item.get("severity", "")).lower() == "critical" for item in findings
        ),
        "highest_impact_findings": ranked_findings[:10],
        "counts": {
            "deterministic_findings": len(deterministic),
            "judge_findings": len(judged),
            "blocks_evaluated": len(blocks),
            "blocks_inspected": block_total,
            "sections_evaluated": len(sections),
            "sections_inspected": (
                section_total
            ),
            "slides_evaluated": len(slides),
            "slides_inspected": (
                slide_total
            ),
        },
        "coverage": {
            "blocks": _coverage_record(len(blocks), block_total, block_complete),
            "sections": _coverage_record(
                len(sections),
                section_total,
                _is_full_coverage(coverage_data, "sections", len(sections), section_total),
            ),
            "slides": _coverage_record(len(slides), slide_total, slide_complete),
        },
        "judge_stability": effective_stability,
        "release_capable": release_capable,
        "usage": dict(usage or {}),
        "drill_down": {
            "blocks": blocks,
            "sections": sections,
            "slides": slides,
        },
        "limitations": [
            *(
                [
                    "Static profile does not assess semantic faithfulness, "
                    "coverage, or slide meaning."
                ]
                if profile == "static"
                else []
            ),
            *(
                [
                    "Smoke profile judges a prioritized sample; scores and issue rates "
                    "do not represent exhaustive lecture coverage."
                ]
                if profile == "smoke"
                else []
            ),
        ],
    }
