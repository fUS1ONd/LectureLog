"""Typed batching contracts for evaluator judges."""

from __future__ import annotations

import json
import os
import random
import re
import unicodedata
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from hashlib import sha256
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lecturelog.evaluation.openrouter import (
    ADJUDICATOR_MODEL,
    TEXT_MODEL,
    VISION_MODEL,
    ContentAddressedCache,
    JudgeCallResult,
    JudgeResponseError,
    ModelRequirement,
    OpenRouterJudgeClient,
)
from lecturelog.evaluation.planner import (
    EvaluationProfile,
    RequestBudget,
    RequestBudgetExceeded,
    plan_judge_batches,
)

PROMPT_VERSION = "v5"


@dataclass(frozen=True)
class JudgePacket:
    stable_id: str
    payload: dict[str, Any]
    validation_context: dict[str, Any] | None = None


class JudgeBatchContractError(ValueError):
    pass


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stable_id: str = Field(
        pattern=r"^(?:note:block|transcript:cue|slide|section|finding):[0-9]+$"
    )
    quote: str = Field(min_length=1, max_length=500)


IssueKind = Literal[
    "faithfulness",
    "content_coverage",
    "language_consistency",
    "clarity",
    "coherence",
    "heading_relevance",
    "information_value",
    "style_consistency",
    "formatting",
    "document_structure",
    "slide_placement",
    "confidence_calibration",
    "insufficient_evidence",
    "other",
]


class RemoteIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stable_id: str = ""
    kind: IssueKind
    code: str
    severity: Literal["info", "warning", "major", "critical"]
    message: str
    evidence: list[EvidenceItem] = Field(min_length=1)


class ItemJudgment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stable_id: str
    score: int = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    evidence: list[EvidenceItem]
    issues: list[RemoteIssue]

    @model_validator(mode="after")
    def evidence_is_required(self) -> ItemJudgment:
        self.issues = [
            issue.model_copy(
                update={
                    "stable_id": issue.stable_id or self.stable_id,
                }
            )
            for issue in self.issues
        ]
        if any(not issue.stable_id for issue in self.issues):
            raise ValueError("every issue requires stable_id")
        if not self.evidence and not self.issues:
            raise ValueError("judgment requires direct evidence or an evidenced issue")
        return self


class BlockJudgment(ItemJudgment):
    faithfulness: int = Field(ge=0, le=100)
    language_consistency: int = Field(ge=0, le=100)
    clarity: int = Field(ge=0, le=100)
    local_coherence: int = Field(ge=0, le=100)
    heading_relevance: int = Field(ge=0, le=100)
    information_value: int = Field(ge=0, le=100)
    style_consistency: int = Field(ge=0, le=100)
    formatting: int = Field(ge=0, le=100)

    @model_validator(mode="after")
    def faithfulness_has_transcript_provenance(self) -> BlockJudgment:
        evidence = [*self.evidence, *(item for issue in self.issues for item in issue.evidence)]
        if not any(
            item.stable_id.startswith("transcript:cue:") for item in evidence
        ) and not any(issue.kind == "insufficient_evidence" for issue in self.issues):
            raise ValueError(
                "block faithfulness requires transcript evidence or insufficient_evidence"
            )
        return self


class SectionJudgment(ItemJudgment):
    content_coverage: int = Field(ge=0, le=100)
    document_structure: int = Field(ge=0, le=100)


class SlideJudgment(ItemJudgment):
    semantic_relevance: int = Field(ge=0, le=100)
    specificity: int = Field(ge=0, le=100)
    candidate_ranking: list[str] = Field(min_length=1)
    anchor_precision: int = Field(default=0, ge=0, le=100)
    current_context_rank: int = Field(default=1, ge=1)
    better_context_id: str | None = None
    placement_verdict: Literal[
        "excellent",
        "correct",
        "acceptable",
        "weak",
        "incorrect",
        "should_be_omitted",
        "missing_but_discussed",
        "uncertain",
    ] = "uncertain"
    system_confidence: Literal["verified", "probable", "fallback", "unresolved", "none"]
    confidence_calibration: int = Field(ge=0, le=100)


class BlockBatchJudgment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    judgments: list[BlockJudgment]


class SectionBatchJudgment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    judgments: list[SectionJudgment]


class SlideBatchJudgment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    judgments: list[SlideJudgment]


class GlobalJudgment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    faithfulness: int = Field(ge=0, le=100)
    content_coverage: int = Field(ge=0, le=100)
    block_quality: int = Field(ge=0, le=100)
    document_structure: int = Field(ge=0, le=100)
    slide_semantic_relevance: int = Field(ge=0, le=100)
    slide_anchor_precision: int = Field(ge=0, le=100)
    confidence_calibration: int = Field(ge=0, le=100)
    confidence: float | None = Field(default=None, ge=0, le=1)
    evidence: list[EvidenceItem] = Field(min_length=1)
    findings: list[RemoteIssue]

    @model_validator(mode="after")
    def faithfulness_has_transcript_provenance(self) -> GlobalJudgment:
        self.findings = [
            finding.model_copy(
                update={
                    "stable_id": finding.stable_id
                    or (finding.evidence[0].stable_id if finding.evidence else "")
                }
            )
            for finding in self.findings
        ]
        if any(not finding.stable_id for finding in self.findings):
            raise ValueError("every global finding requires evidence and stable_id")
        evidence = [
            *self.evidence,
            *(item for finding in self.findings for item in finding.evidence),
        ]
        if not any(
            item.stable_id.startswith("transcript:cue:") for item in evidence
        ) and not any(finding.kind == "insufficient_evidence" for finding in self.findings):
            raise ValueError(
                "global faithfulness requires transcript evidence or insufficient_evidence"
            )
        return self


class EvaluationJudges:
    def __init__(self, client: OpenRouterJudgeClient):
        self.client = client

    async def blocks(
        self, packets: list[JudgePacket], schema: type[BaseModel]
    ) -> JudgeCallResult:
        return await self._text_batch("block", packets, schema, minimum=1, maximum=10)

    async def sections(
        self, packets: list[JudgePacket], schema: type[BaseModel]
    ) -> JudgeCallResult:
        return await self._text_batch("section", packets, schema, minimum=1, maximum=6)

    async def slides(
        self,
        packets: list[JudgePacket],
        schema: type[BaseModel],
        *,
        images: list[str] | None = None,
    ) -> JudgeCallResult:
        _validate_batch("slide", packets, 1, 6)
        vision = bool(images)
        if images and len(images) > len(packets):
            raise JudgeBatchContractError("Slide image count cannot exceed packet count")
        return await self.client.judge(
            model=VISION_MODEL if vision else TEXT_MODEL,
            requirement=ModelRequirement(image_input=vision),
            prompt=_render_prompt("slide", packets),
            schema=schema,
            images=images,
            prompt_version=PROMPT_VERSION,
            validate_value=lambda value: _validate_response(value, packets),
        )

    async def global_document(
        self, packet: JudgePacket, schema: type[BaseModel]
    ) -> JudgeCallResult:
        return await self.client.judge(
            model=TEXT_MODEL,
            requirement=ModelRequirement(),
            prompt=_render_prompt("global", [packet]),
            schema=schema,
            prompt_version=PROMPT_VERSION,
            validate_value=lambda value: _validate_response(value, [packet]),
        )

    async def adjudicate(
        self, packet: JudgePacket, schema: type[BaseModel], *, images: list[str] | None = None
    ) -> JudgeCallResult:
        return await self.client.judge(
            model=ADJUDICATOR_MODEL,
            requirement=ModelRequirement(image_input=bool(images)),
            prompt=_render_prompt("adjudication", [packet]),
            schema=schema,
            images=images,
            prompt_version=PROMPT_VERSION,
            validate_value=lambda value: _validate_response(value, [packet]),
        )

    async def _text_batch(
        self,
        kind: str,
        packets: list[JudgePacket],
        schema: type[BaseModel],
        *,
        minimum: int,
        maximum: int,
    ) -> JudgeCallResult:
        _validate_batch(kind, packets, minimum, maximum)
        return await self.client.judge(
            model=TEXT_MODEL,
            requirement=ModelRequirement(),
            prompt=_render_prompt(kind, packets),
            schema=schema,
            prompt_version=PROMPT_VERSION,
            validate_value=lambda value: _validate_response(value, packets),
        )


def _validate_batch(
    kind: str, packets: list[JudgePacket], minimum: int, maximum: int
) -> None:
    if not minimum <= len(packets) <= maximum:
        raise JudgeBatchContractError(
            f"{kind} judge requires {minimum}..{maximum} packets, got {len(packets)}"
        )
    ids = [packet.stable_id for packet in packets]
    if any(not stable_id for stable_id in ids) or len(ids) != len(set(ids)):
        raise JudgeBatchContractError(f"{kind} packets require unique non-empty stable IDs")


def _validate_batch_response(value: BaseModel, packets: list[JudgePacket]) -> None:
    judgments = getattr(value, "judgments", None)
    if not isinstance(judgments, list):
        raise JudgeResponseError("Batch judge response omitted judgments")
    expected = [packet.stable_id for packet in packets]
    aliases = {
        f"{prefix}{packet.stable_id}": packet.stable_id
        for packet in packets
        for prefix in ("note:block:", "section:", "slide:")
    }
    for judgment in judgments:
        # Some structured-output models copy the typed evidence namespace into
        # the packet ID. Accept only an exact, unambiguous known prefix; quotes
        # and evidence source IDs remain subject to their original strict checks.
        judgment.stable_id = aliases.get(judgment.stable_id, judgment.stable_id)
    actual = [judgment.stable_id for judgment in judgments]
    if len(actual) != len(expected) or len(actual) != len(set(actual)) or set(actual) != set(
        expected
    ):
        raise JudgeResponseError(
            f"Batch judge stable_ids mismatch: expected exact unique IDs {expected}, got {actual}"
        )
    by_id = {judgment.stable_id: judgment for judgment in judgments}
    value.judgments = [by_id[stable_id] for stable_id in expected]


def _normalize_quote(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _packet_sources(packet: JudgePacket) -> dict[str, str]:
    source_map = packet.payload.get("source_map", [])
    if not isinstance(source_map, list):
        raise JudgeResponseError(f"Packet {packet.stable_id} has invalid source_map")
    sources: dict[str, str] = {}
    for source in source_map:
        if not isinstance(source, dict):
            continue
        stable_id, text = source.get("stable_id"), source.get("text")
        if isinstance(stable_id, str) and isinstance(text, str):
            if stable_id in sources:
                raise JudgeResponseError(f"Duplicate evidence source ID: {stable_id}")
            sources[stable_id] = text
    return sources


def _iter_evidence(value: BaseModel) -> list[EvidenceItem]:
    evidence: list[EvidenceItem] = []
    judgments = getattr(value, "judgments", None)
    owners = judgments if isinstance(judgments, list) else [value]
    for owner in owners:
        evidence.extend(getattr(owner, "evidence", ()))
        for issue in getattr(owner, "issues", getattr(owner, "findings", ())):
            evidence.extend(issue.evidence)
    return evidence


def _validate_response(value: BaseModel, packets: list[JudgePacket]) -> None:
    if hasattr(value, "judgments"):
        _validate_batch_response(value, packets)
        _validate_slide_rankings(value, packets)
    sources: dict[str, str] = {}
    for packet in packets:
        for stable_id, text in _packet_sources(packet).items():
            if stable_id in sources and sources[stable_id] != text:
                raise JudgeResponseError(f"Evidence source namespace collision: {stable_id}")
            sources[stable_id] = text
    for evidence in _iter_evidence(value):
        source = sources.get(evidence.stable_id)
        if source is None:
            raise JudgeResponseError(f"Unknown evidence ID: {evidence.stable_id}")
        quote = _normalize_quote(evidence.quote)
        if not quote or quote not in _normalize_quote(source):
            raise JudgeResponseError(
                f"Evidence quote does not match exact source {evidence.stable_id}"
            )


def _validate_slide_rankings(value: BaseModel, packets: list[JudgePacket]) -> None:
    judgments = getattr(value, "judgments", ())
    by_id = {packet.stable_id: packet for packet in packets}
    for judgment in judgments:
        if not isinstance(judgment, SlideJudgment):
            continue
        packet = by_id[judgment.stable_id]
        private_context = packet.validation_context or {}
        judgment.system_confidence = private_context.get(
            "system_confidence", judgment.system_confidence
        )
        candidate_ids = [
            candidate["candidate_id"]
            for candidate in packet.payload.get("candidate_contexts", ())
        ]
        ranking = judgment.candidate_ranking
        if (
            len(ranking) != len(candidate_ids)
            or len(ranking) != len(set(ranking))
            or set(ranking) != set(candidate_ids)
        ):
            raise JudgeResponseError(
                f"Slide {judgment.stable_id} candidate_ranking must contain exact unique IDs"
            )
        current_id = private_context.get("evaluated_candidate_id")
        if current_id not in ranking:
            raise JudgeResponseError(
                f"Slide {judgment.stable_id} private evaluated candidate is missing"
            )
        rank = ranking.index(current_id) + 1
        judgment.current_context_rank = rank
        judgment.better_context_id = ranking[0] if rank != 1 else None
        count = len(ranking)
        judgment.anchor_precision = (
            100 if count == 1 else round(100 * (count - rank) / (count - 1))
        )
        if rank == 1:
            judgment.placement_verdict = (
                "excellent"
                if judgment.semantic_relevance >= 85 and judgment.specificity >= 75
                else "correct"
            )
        elif rank == count:
            judgment.placement_verdict = "incorrect"
        elif rank <= max(2, (count + 2) // 3):
            judgment.placement_verdict = "acceptable"
        else:
            judgment.placement_verdict = "weak"


def _render_prompt(kind: str, packets: list[JudgePacket]) -> str:
    template = (
        files("lecturelog.evaluation.prompts")
        .joinpath(PROMPT_VERSION, f"{kind}.txt")
        .read_text(encoding="utf-8")
    )
    body = [{"stable_id": packet.stable_id, **packet.payload} for packet in packets]
    return f"{template}\n\nВХОДНЫЕ ПАКЕТЫ JSON:\n{json.dumps(body, ensure_ascii=False)}"


async def run_planned_evaluation(
    artifacts: Any,
    profile: EvaluationProfile | str,
    max_requests: int | None,
    cache_dir: Path,
    resume: bool,
    *,
    allow_remote: Callable[[], bool],
    deterministic_findings: Any | None = None,
    api_key: str | None = None,
    client: OpenRouterJudgeClient | None = None,
) -> dict[str, Any]:
    """Execute the remote portion end-to-end, sequentially and resumably.

    ``resume`` is recorded for the manifest-facing caller. Safe content cache
    hits are always reused: identical judge work must never spend free quota.
    """

    selected = EvaluationProfile(profile)
    budget = RequestBudget(selected, max_requests)
    remote = client or OpenRouterJudgeClient(
        api_key=api_key if api_key is not None else os.getenv("OPENROUTER_API_KEY", ""),
        cache=ContentAddressedCache(cache_dir),
        budget=budget,
        allow_remote=allow_remote,
    )
    judges = EvaluationJudges(remote)
    blocks = list(getattr(artifacts, "blocks", ()))
    sections = list(getattr(artifacts, "sections", ()))
    slides = list(getattr(artifacts, "slides", ()))
    plan = plan_judge_batches(
        selected,
        blocks=blocks,
        sections=sections,
        slides=slides,
        alignment=getattr(artifacts, "alignment", None),
    )
    indexes = {
        "block": {str(item.block_id): item for item in blocks},
        "section": {str(item.section_id): item for item in sections},
        "slide": {
            str(getattr(item, "slide_number", getattr(item, "slide_num", ""))): item
            for item in slides
        },
    }
    output: dict[str, Any] = {
        "blocks": [],
        "sections": [],
        "slides": [],
        "global": [],
        "calls": [],
        "attempts": [],
        "usage": {"requests": 0, "prompt_tokens": 0, "completion_tokens": 0},
        "incomplete_reasons": [],
        "resume_requested": resume,
    }
    local_results: list[dict[str, Any]] = []
    for call in plan:
        try:
            if call.kind == "global":
                global_payload = _global_packet_payload(
                    artifacts,
                    local_results,
                    getattr(artifacts, "load_findings", ())
                    if deterministic_findings is None
                    else deterministic_findings,
                )
                packet = JudgePacket(
                    "document",
                    global_payload,
                )
                result = await judges.global_document(packet, GlobalJudgment)
            else:
                packets, images = _call_packets(
                    call.kind, call.item_ids, indexes[call.kind], artifacts
                )
                method = getattr(judges, f"{call.kind}s")
                schema = {
                    "block": BlockBatchJudgment,
                    "section": SectionBatchJudgment,
                    "slide": SlideBatchJudgment,
                }[call.kind]
                result = (
                    await method(packets, schema, images=images or None)
                    if call.kind == "slide"
                    else await method(packets, schema)
                )
            serialized = result.value.model_dump(mode="json")
            destination = "global" if call.kind == "global" else f"{call.kind}s"
            if call.kind == "global":
                output[destination].append(serialized)
            else:
                output[destination].extend(serialized["judgments"])
                local_results.extend(serialized["judgments"])
            output["calls"].append(
                {
                    "kind": call.kind,
                    "requested_model": result.requested_model,
                    "actual_model": result.actual_model,
                    "actual_model_reported": getattr(
                        result, "actual_model_reported", True
                    ),
                    "cache_key": result.cache_key,
                    "cached": result.cached,
                    "normalization_warnings": list(
                        getattr(result, "normalization_warnings", ())
                    ),
                }
            )
            output["usage"]["prompt_tokens"] += result.prompt_tokens
            output["usage"]["completion_tokens"] += result.completion_tokens
        except RequestBudgetExceeded as error:
            output["incomplete_reasons"].append(str(error))
            break
        except Exception as error:
            output["incomplete_reasons"].append(f"{call.kind}: {type(error).__name__}: {error}")
            break
    output["usage"]["requests"] = remote.budget.used
    output["attempts"] = list(getattr(remote, "attempt_records", ()))
    output["incomplete"] = bool(output["incomplete_reasons"])
    return output


def _call_packets(
    kind: str,
    item_ids: tuple[str, ...],
    index: dict[str, Any],
    artifacts: Any,
) -> tuple[list[JudgePacket], list[str]]:
    packets: list[JudgePacket] = []
    images: list[str] = []
    for stable_id in item_ids:
        item = index[stable_id]
        validation_context: dict[str, Any] = {}
        payload = _packet_payload(
            kind, item, artifacts, validation_context=validation_context
        )
        if kind == "slide":
            image = getattr(item, "image_data_url", None)
            if (
                isinstance(image, str)
                and image
                and getattr(item, "native_text_quality", "none") != "good"
            ):
                payload["image_ref"] = f"uploaded_image:{len(images)}"
                images.append(image)
        packets.append(
            JudgePacket(stable_id, payload, validation_context or None)
        )
    return packets, images


def _packet_payload(
    kind: str,
    item: Any,
    artifacts: Any,
    *,
    validation_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = _jsonable(item)
    sources: dict[str, str] = {}
    transcript = list(getattr(artifacts, "transcript", ()))
    if kind == "section":
        evidence = _time_window(
            transcript, item.start_s, item.end_s, limit=30, stratified=True
        )
        sources[f"section:{item.section_id}"] = item.content_md
    elif kind == "block":
        section = next(
            (
                candidate
                for candidate in getattr(artifacts, "sections", ())
                if candidate.section_id == getattr(item, "section_id", None)
            ),
            None,
        )
        if section is not None:
            payload["section_context"] = section.content_md[:4000]
            evidence = _block_transcript_context(item.text, transcript, section)
            sources[f"section:{section.section_id}"] = section.content_md
        else:
            evidence = _lexical_candidates(item.text, transcript, limit=12)
        sources[f"note:block:{item.block_id}"] = item.text
    elif kind == "slide":
        _evidence, placement, candidates, evaluated_id = _slide_context(
            item, artifacts, transcript
        )
        payload["candidate_contexts"] = candidates
        if validation_context is not None:
            validation_context["evaluated_candidate_id"] = evaluated_id
            validation_context["system_confidence"] = placement["system_confidence"]
            validation_context["placement_metadata"] = placement
        # Ranking must remain blind: do not expose the current placement,
        # anchor, or a duplicate current-context evidence list beside the
        # shuffled opaque candidates.
        evidence = []
        sources[f"slide:{getattr(item, 'slide_num', getattr(item, 'slide_number', ''))}"] = (
            getattr(item, "native_text", "")
        )
    else:
        raise ValueError(f"Unknown packet kind: {kind}")
    payload["transcript_evidence"] = [_jsonable(cue) for cue in evidence]
    for cue in evidence:
        sources[f"transcript:cue:{cue.block_id}"] = cue.text
    for candidate in payload.get("candidate_contexts", ()):
        for cue in candidate.get("context", ()):
            sources[f"transcript:cue:{cue['block_id']}"] = cue["text"]
    payload["source_map"] = [
        {"stable_id": stable_id, "text": text} for stable_id, text in sources.items()
    ]
    return _bound_payload(payload)


def _block_transcript_context(text: str, transcript: list[Any], section: Any) -> list[Any]:
    window = _time_window(transcript, section.start_s, section.end_s, limit=len(transcript))
    if not window:
        return _lexical_candidates(text, transcript, limit=12)
    ranked = _lexical_candidates(text, window, limit=8)
    if not ranked:
        return window[:12]
    positions = {int(cue.block_id): index for index, cue in enumerate(window)}
    selected: set[int] = set()
    for cue in ranked:
        center = positions[int(cue.block_id)]
        selected.update(range(max(0, center - 1), min(len(window), center + 2)))
    return [window[index] for index in sorted(selected)[:24]]


def _time_window(
    transcript: list[Any],
    start_s: float | None,
    end_s: float | None,
    *,
    limit: int,
    stratified: bool = False,
) -> list[Any]:
    if start_s is None or end_s is None:
        return []
    selected = [
        cue
        for cue in transcript
        if float(cue.end_s) >= float(start_s) and float(cue.start_s) <= float(end_s)
    ]
    if not stratified or len(selected) <= limit:
        return selected[:limit]
    if limit <= 1:
        return selected[:limit]
    indexes = {
        round(index * (len(selected) - 1) / (limit - 1)) for index in range(limit)
    }
    return [selected[index] for index in sorted(indexes)]


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[A-Za-zА-Яа-яЁё]{3,}", text.casefold())
        if token not in {"который", "которая", "этого", "this", "that", "with", "from"}
    }


def _lexical_candidates(text: str, transcript: list[Any], *, limit: int) -> list[Any]:
    query = _tokens(text)
    ranked = sorted(
        transcript,
        key=lambda cue: len(query & _tokens(cue.text)) / max(1, len(query | _tokens(cue.text))),
        reverse=True,
    )
    return [cue for cue in ranked[:limit] if query & _tokens(cue.text)]


def _alternative_windows(
    text: str, transcript: list[Any], excluded_ids: set[int], *, limit: int
) -> list[dict[str, Any]]:
    document_frequency: dict[str, int] = {}
    for cue in transcript:
        for token in _tokens(cue.text):
            document_frequency[token] = document_frequency.get(token, 0) + 1
    common = {
        token
        for token, count in document_frequency.items()
        if len(transcript) >= 6 and count / len(transcript) >= 0.4
    }
    query = _tokens(text) - common
    ranked: list[tuple[float, int]] = []
    for index, cue in enumerate(transcript):
        cue_tokens = _tokens(cue.text) - common
        overlap = query & cue_tokens
        if overlap and int(cue.block_id) not in excluded_ids:
            ranked.append((len(overlap) / max(1, len(query | cue_tokens)), index))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    alternatives: list[dict[str, Any]] = []
    occupied: set[int] = set()
    for score, center in ranked:
        start, end = max(0, center - 2), min(len(transcript), center + 3)
        window_ids = {int(cue.block_id) for cue in transcript[start:end]}
        if window_ids & excluded_ids or window_ids & occupied:
            continue
        alternatives.append(
            {
                "lexical_score": round(score, 4),
                "context": [_jsonable(cue) for cue in transcript[start:end]],
            }
        )
        occupied.update(window_ids)
        if len(alternatives) == limit:
            break
    return alternatives


def _random_negative_window(
    transcript: list[Any], excluded_ids: set[int], *, seed: str
) -> dict[str, Any] | None:
    eligible = [
        index
        for index, cue in enumerate(transcript)
        if int(cue.block_id) not in excluded_ids
    ]
    if not eligible:
        return None
    center = random.Random(seed).choice(eligible)
    start, end = max(0, center - 2), min(len(transcript), center + 3)
    context = [
        cue for cue in transcript[start:end] if int(cue.block_id) not in excluded_ids
    ]
    if not context:
        return None
    return {"context": [_jsonable(cue) for cue in context]}


def _slide_context(
    slide: Any, artifacts: Any, transcript: list[Any]
) -> tuple[list[Any], dict[str, Any], list[dict[str, Any]], str]:
    slide_num = getattr(slide, "slide_num", getattr(slide, "slide_number", None))
    alignment = getattr(artifacts, "alignment", None)
    records = list(getattr(alignment, "assignments", ())) + list(
        getattr(alignment, "placements", ())
    )
    matching = [
        record
        for record in records
        if record.get("slide_num", record.get("slide_number")) == slide_num
    ]
    evidence_ids: set[int] = set()
    anchor_id: int | None = None
    anchor_s: float | None = None
    for record in matching:
        for value in record.get("evidence_block_ids", ()):
            try:
                evidence_ids.add(int(value))
            except (TypeError, ValueError):
                continue
        candidate = record.get("anchor_block_id", record.get("block_id"))
        if candidate is not None:
            with suppress(TypeError, ValueError):
                anchor_id = int(candidate)
        if record.get("anchor_s") is not None:
            with suppress(TypeError, ValueError):
                anchor_s = float(record["anchor_s"])
    positions = {int(cue.block_id): index for index, cue in enumerate(transcript)}
    selected_indexes = {positions[value] for value in evidence_ids if value in positions}
    if anchor_id in positions:
        anchor_index = positions[anchor_id]
        selected_indexes.update(
            range(max(0, anchor_index - 3), min(len(transcript), anchor_index + 4))
        )
    elif anchor_s is not None and transcript:
        anchor_index = min(
            range(len(transcript)),
            key=lambda index: abs(float(transcript[index].start_s) - anchor_s),
        )
        selected_indexes.update(
            range(max(0, anchor_index - 3), min(len(transcript), anchor_index + 4))
        )
    evidence = [transcript[index] for index in sorted(selected_indexes)][:20]
    current_ids = {int(cue.block_id) for cue in evidence}
    ranked_alternatives = _alternative_windows(
        getattr(slide, "native_text", ""), transcript, current_ids, limit=8
    )
    selected_alternatives = ranked_alternatives[:4]
    if len(ranked_alternatives) > 4:
        # A lower-ranked lexical match is a harder decoy than another top candidate.
        selected_alternatives.append(ranked_alternatives[-1])
    occupied_ids = set(current_ids)
    for alternative in selected_alternatives:
        occupied_ids.update(int(cue["block_id"]) for cue in alternative["context"])
    negative = _random_negative_window(
        transcript,
        occupied_ids,
        seed=f"negative:{slide_num}:{getattr(slide, 'native_text', '')}",
    )
    raw_candidates: list[dict[str, Any]] = [
        {"is_evaluated": True, "context": [_jsonable(cue) for cue in evidence]},
        *[
            {"is_evaluated": False, "context": alternative["context"]}
            for alternative in selected_alternatives
        ],
    ]
    if negative is not None:
        raw_candidates.append({"is_evaluated": False, "context": negative["context"]})
    random.Random(
        sha256(
            f"shuffle:{slide_num}:{getattr(slide, 'native_text', '')}".encode()
        ).hexdigest()
    ).shuffle(raw_candidates)
    candidates: list[dict[str, Any]] = []
    evaluated_candidate_id = ""
    for index, candidate in enumerate(raw_candidates, start=1):
        candidate_id = f"candidate-{index}"
        candidates.append({"candidate_id": candidate_id, "context": candidate["context"]})
        if candidate["is_evaluated"]:
            evaluated_candidate_id = candidate_id
    placement = {
        "slide_num": slide_num,
        "alignment_records": matching[:4],
        "anchor_block_id": anchor_id,
        "anchor_s": anchor_s,
        "system_confidence": _system_confidence(matching),
    }
    placement_record = next(
        (record for record in matching if record.get("output_kind") is not None), None
    )
    if placement_record is not None:
        section_id = placement_record.get("global_section_id")
        section = next(
            (
                candidate
                for candidate in getattr(artifacts, "sections", ())
                if candidate.section_id == section_id
            ),
            None,
        )
        if section is not None:
            placement["note_context"] = {
                "section_id": section.section_id,
                "content_md": section.content_md[:4000],
            }
    return evidence, placement, candidates, evaluated_candidate_id


def _system_confidence(records: list[dict[str, Any]]) -> str:
    allowed = {"verified", "probable", "fallback", "unresolved", "none"}
    for field in ("anchor_confidence", "assignment_confidence"):
        for record in reversed(records):
            value = record.get(field)
            if value in allowed:
                return str(value)
    return "none"


def _artifact_source_index(artifacts: Any) -> dict[str, str]:
    sources: dict[str, str] = {}
    for block in getattr(artifacts, "blocks", ()):
        sources[f"note:block:{block.block_id}"] = block.text
    for cue in getattr(artifacts, "transcript", ()):
        sources[f"transcript:cue:{cue.block_id}"] = cue.text
    for slide in getattr(artifacts, "slides", ()):
        number = getattr(slide, "slide_num", getattr(slide, "slide_number", ""))
        sources[f"slide:{number}"] = getattr(slide, "native_text", "")
    for section in getattr(artifacts, "sections", ()):
        sources[f"section:{section.section_id}"] = section.content_md
    return sources


def _result_evidence(local_results: list[dict[str, Any]]) -> dict[str, list[str]]:
    referenced: dict[str, list[str]] = {}
    for result in local_results:
        owners = [result, *result.get("issues", ())]
        for owner in owners:
            for evidence in owner.get("evidence", ()):
                stable_id, quote = evidence.get("stable_id"), evidence.get("quote")
                if isinstance(stable_id, str) and isinstance(quote, str):
                    referenced.setdefault(stable_id, []).append(quote)
    return referenced


def _compact_referenced_source(text: str, quotes: list[str], *, limit: int = 1600) -> str:
    if len(text) <= limit:
        return text
    excerpts = [
        quote.strip()
        for quote in quotes
        if quote.strip() and _normalize_quote(quote) in _normalize_quote(text)
    ]
    compact = "\n…\n".join(dict.fromkeys(excerpts))
    return compact[:limit] if compact else text[:limit]


def _global_packet_payload(
    artifacts: Any,
    local_results: list[dict[str, Any]],
    deterministic_findings: Any,
) -> dict[str, Any]:
    source_index = _artifact_source_index(artifacts)
    referenced = _result_evidence(local_results)
    source_map = [
        {
            "stable_id": stable_id,
            "text": _compact_referenced_source(source_index[stable_id], quotes),
        }
        for stable_id, quotes in referenced.items()
        if stable_id in source_index
    ][:80]
    findings_payload: list[dict[str, Any]] = []
    for index, finding in enumerate(list(deterministic_findings)[:40], start=1):
        structured = _jsonable(finding)
        if not isinstance(structured, dict):
            structured = {"message": str(structured)}
        stable_id = f"finding:{index}"
        structured = {"stable_id": stable_id, **structured}
        findings_payload.append(structured)
        source_map.append(
            {
                "stable_id": stable_id,
                "text": json.dumps(
                    {
                        key: structured.get(key)
                        for key in ("code", "severity", "message", "evidence")
                        if structured.get(key) not in (None, [], ())
                    },
                    ensure_ascii=False,
                )[:1600],
            }
        )
    return _bound_payload(
        {
            "local_results": local_results,
            "deterministic_findings": findings_payload,
            "source_map": source_map[:120],
        }
    )


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "__dataclass_fields__"):
        from dataclasses import asdict

        return asdict(value)
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


def _bound_payload(value: Any) -> Any:
    if isinstance(value, str):
        return value[:6000]
    if isinstance(value, list):
        return [_bound_payload(item) for item in value[:40]]
    if isinstance(value, tuple):
        return [_bound_payload(item) for item in value[:40]]
    if isinstance(value, dict):
        return {
            str(key): (
                [_bound_payload(source) for source in item[:120]]
                if key == "source_map" and isinstance(item, list)
                else _bound_payload(item)
            )
            for key, item in value.items()
        }
    return value
