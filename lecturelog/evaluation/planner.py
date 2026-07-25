"""Deterministic remote-call planning and hard request budgets."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum


class EvaluationProfile(StrEnum):
    STATIC = "static"
    SMOKE = "smoke"
    STANDARD = "standard"
    DEEP = "deep"


@dataclass(frozen=True)
class ProfilePolicy:
    hard_cap: int
    block_batch_size: int
    section_batch_size: int
    slide_batch_size: int
    include_global: bool
    allow_adjudication: bool


PROFILE_POLICIES: dict[EvaluationProfile, ProfilePolicy] = {
    EvaluationProfile.STATIC: ProfilePolicy(0, 0, 0, 0, False, False),
    EvaluationProfile.SMOKE: ProfilePolicy(8, 4, 4, 2, True, False),
    # Adjudication is not implemented by the current runner. Never advertise it.
    EvaluationProfile.STANDARD: ProfilePolicy(24, 4, 5, 3, True, False),
    EvaluationProfile.DEEP: ProfilePolicy(45, 4, 4, 4, True, False),
}


class RequestBudgetExceeded(RuntimeError):
    """Raised before a request that would exceed the configured hard cap."""


class RequestBudget:
    def __init__(self, profile: EvaluationProfile | str, max_requests: int | None = None):
        self.profile = EvaluationProfile(profile)
        profile_cap = PROFILE_POLICIES[self.profile].hard_cap
        if max_requests is not None and max_requests < 0:
            raise ValueError("max_requests cannot be negative")
        self.limit = min(profile_cap, max_requests) if max_requests is not None else profile_cap
        self.used = 0

    @property
    def remaining(self) -> int:
        return self.limit - self.used

    def consume(self) -> None:
        if self.used >= self.limit:
            raise RequestBudgetExceeded(
                f"Remote request cap reached ({self.used}/{self.limit}); "
                "resume later with the same cache"
            )
        self.used += 1


@dataclass(frozen=True)
class JudgeBatch:
    kind: str
    item_ids: tuple[str, ...]


@dataclass(frozen=True)
class Coverage:
    kind: str
    planned: int
    total: int
    mode: str

    @property
    def exhaustive(self) -> bool:
        return self.planned == self.total and self.mode == "exhaustive"


@dataclass(frozen=True)
class PlanMetadata:
    coverage: tuple[Coverage, ...]
    logical_requests: int
    worst_case_physical_requests: int
    retry_attempts_per_cache_miss: int
    assumed_cache_misses: int
    release_capable: bool
    release_incapable_reasons: tuple[str, ...]
    stability_repeats_planned: int = 0
    adjudications_planned: int = 0


class EvaluationPlan(list[JudgeBatch]):
    """List-compatible plan with explicit product/release semantics."""

    def __init__(self, batches: Iterable[JudgeBatch], metadata: PlanMetadata):
        super().__init__(batches)
        self.metadata = metadata


def _ids[T](items: Iterable[T], *, id_attr: str) -> list[str]:
    result = []
    for item in items:
        value = getattr(item, id_attr, item if isinstance(item, (str, int)) else None)
        if value is None and id_attr == "slide_number":
            value = getattr(item, "slide_num", None)
        if value is None:
            raise ValueError(f"Item has no stable {id_attr!r}: {item!r}")
        result.append(str(value))
    return result


def _batches(kind: str, ids: list[str], size: int) -> list[JudgeBatch]:
    if not ids or size == 0:
        return []
    return [
        JudgeBatch(kind=kind, item_ids=tuple(ids[offset : offset + size]))
        for offset in range(0, len(ids), size)
    ]


def _stratified[T](items: list[T], limit: int) -> list[T]:
    if limit <= 0 or not items:
        return []
    if len(items) <= limit:
        return items
    if limit == 1:
        return [items[len(items) // 2]]
    indexes = [round(index * (len(items) - 1) / (limit - 1)) for index in range(limit)]
    return [items[index] for index in indexes]


def _sample_blocks(profile: EvaluationProfile, blocks: list[object]) -> list[object]:
    limits = {
        EvaluationProfile.SMOKE: 4,
        EvaluationProfile.STANDARD: 24,
        EvaluationProfile.DEEP: 48,
    }
    limit = limits.get(profile, 0)
    excluded = {"heading", "metadata", "image", "code"}
    content = [
        block
        for block in blocks
        if str(getattr(block, "kind", "")) not in excluded and not _is_toc_block(block)
    ]
    preferred = [block for block in content if len(str(getattr(block, "text", ""))) >= 80]
    selected = _stratified(preferred, min(limit, len(preferred)))
    selected_ids = {id(item) for item in selected}
    if len(selected) < limit:
        remaining = [item for item in content if id(item) not in selected_ids]
        selected.extend(_stratified(remaining, limit - len(selected)))
    order = {id(item): index for index, item in enumerate(blocks)}
    return sorted(selected, key=lambda item: order[id(item)])


def _is_toc_block(block: object) -> bool:
    heading_path = " / ".join(str(value) for value in getattr(block, "heading_path", ())).casefold()
    if "оглавление" in heading_path or "table of contents" in heading_path:
        return True
    text = str(getattr(block, "text", ""))
    wikilinks = re.findall(r"\[\[[^\]]+\]\]", text)
    nonempty_lines = [line for line in text.splitlines() if line.strip()]
    return (
        str(getattr(block, "kind", "")) == "list"
        and len(wikilinks) >= 2
        and len(wikilinks) >= max(2, len(nonempty_lines) - 1)
    )


def _sample_slides(
    profile: EvaluationProfile, slides: list[object], alignment: object | None
) -> list[object]:
    if profile is EvaluationProfile.DEEP:
        return slides
    limit = 2 if profile is EvaluationProfile.SMOKE else 12
    assignments = list(getattr(alignment, "assignments", ()))
    sections = Counter(record.get("global_section_id") for record in assignments)
    anchors = Counter(
        evidence_id
        for record in assignments
        for evidence_id in record.get("evidence_block_ids", ())
    )
    suspicious: dict[int, float] = {}
    for record in assignments:
        slide_num = record.get("slide_num")
        if slide_num is None:
            continue
        score = float(record.get("score") or 0)
        risk = max(0.0, 1.0 - score)
        section_id = record.get("global_section_id")
        if section_id is not None and sections[section_id] >= 4:
            risk += sections[section_id] / 10
        if any(anchors[value] > 1 for value in record.get("evidence_block_ids", ())):
            risk += 1
        if risk > 0.35:
            suspicious[int(slide_num)] = risk
    by_num = {
        int(getattr(slide, "slide_num", getattr(slide, "slide_number", -1))): slide
        for slide in slides
    }
    priority_limit = limit if profile is EvaluationProfile.SMOKE else max(1, limit // 2)
    priority = [
        by_num[number]
        for number, _risk in sorted(suspicious.items(), key=lambda item: (-item[1], item[0]))
        if number in by_num
    ][:priority_limit]
    priority_ids = {id(item) for item in priority}
    remaining = [item for item in slides if id(item) not in priority_ids]
    return priority + _stratified(remaining, limit - len(priority))


def plan_judge_batches(
    profile: EvaluationProfile | str,
    *,
    blocks: Iterable[object] = (),
    sections: Iterable[object] = (),
    slides: Iterable[object] = (),
    alignment: object | None = None,
) -> EvaluationPlan:
    """Build a stable, sequential call plan.

    Upstream retrieval should pass representative/suspicious items in priority
    order. Smoke deliberately samples only one batch of each expensive kind.
    """

    selected = EvaluationProfile(profile)
    policy = PROFILE_POLICIES[selected]
    all_blocks, all_sections, all_slides = list(blocks), list(sections), list(slides)
    if selected is EvaluationProfile.STATIC:
        coverage = tuple(
            Coverage(kind, 0, total, "deterministic_only")
            for kind, total in (
                ("block", len(all_blocks)),
                ("section", len(all_sections)),
                ("slide", len(all_slides)),
            )
        )
        metadata = PlanMetadata(
            coverage, 0, 0, 0, 0, False, ("static profile has no semantic judges",)
        )
        return EvaluationPlan((), metadata)
    selected_blocks = _sample_blocks(selected, all_blocks)
    selected_sections = (
        all_sections
        if selected is EvaluationProfile.DEEP
        else _stratified(
            all_sections,
            {EvaluationProfile.SMOKE: 4, EvaluationProfile.STANDARD: 10}[selected],
        )
    )
    selected_slides = _sample_slides(selected, all_slides, alignment)
    block_batches = _batches(
        "block", _ids(selected_blocks, id_attr="block_id"), policy.block_batch_size
    )
    section_batches = _batches(
            "section",
            _ids(selected_sections, id_attr="section_id"),
            policy.section_batch_size,
        )
    slide_batches = _batches(
        "slide", _ids(selected_slides, id_attr="slide_number"), policy.slide_batch_size
    )
    # Release-relevant exhaustive dimensions get the deep budget before the
    # intentionally sampled block-quality dimension.
    groups = (
        [section_batches, slide_batches, block_batches]
        if selected is EvaluationProfile.DEEP
        else [block_batches, section_batches, slide_batches]
    )
    result = [batch for group in groups for batch in group]
    if policy.include_global:
        result = result[: max(0, policy.hard_cap - 1)]
        result.append(JudgeBatch(kind="global", item_ids=("document",)))
    elif len(result) > policy.hard_cap:
        result = result[: policy.hard_cap]
    planned_ids = {
        kind: {
            item_id
            for batch in result
            if batch.kind == kind
            for item_id in batch.item_ids
        }
        for kind in ("block", "section", "slide")
    }
    totals = {
        "block": len(
            [
                item
                for item in all_blocks
                if str(getattr(item, "kind", "")) not in {"heading", "metadata", "image", "code"}
                and not _is_toc_block(item)
            ]
        ),
        "section": len(all_sections),
        "slide": len(all_slides),
    }
    coverage = tuple(
        Coverage(
            kind=kind,
            planned=len(planned_ids[kind]),
            total=totals[kind],
            mode=(
                "exhaustive"
                if selected is EvaluationProfile.DEEP
                and len(planned_ids[kind]) == totals[kind]
                and kind in {"section", "slide"}
                else "sampled_stratified"
                if selected is EvaluationProfile.DEEP and kind == "block"
                else "sampled_directional"
            ),
        )
        for kind in ("block", "section", "slide")
    )
    reasons: list[str] = []
    if selected is not EvaluationProfile.DEEP:
        reasons.append(f"{selected.value} coverage is sampled_directional")
    if selected is EvaluationProfile.DEEP:
        for item in coverage:
            if item.kind in {"section", "slide"} and not item.exhaustive:
                reasons.append(
                    f"deep {item.kind} coverage is incomplete ({item.planned}/{item.total})"
                )
        reasons.append("stability repeats and adjudication are not executed by the current runner")
    retry_attempts = 2
    logical_requests = len(result)
    metadata = PlanMetadata(
        coverage=coverage,
        logical_requests=logical_requests,
        worst_case_physical_requests=min(
            policy.hard_cap,
            logical_requests * (1 + retry_attempts),
        ),
        retry_attempts_per_cache_miss=retry_attempts,
        assumed_cache_misses=logical_requests,
        release_capable=selected is EvaluationProfile.DEEP and not reasons,
        release_incapable_reasons=tuple(reasons),
    )
    return EvaluationPlan(result, metadata)
