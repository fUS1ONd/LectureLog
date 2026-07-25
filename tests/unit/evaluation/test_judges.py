from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ValidationError

from lecturelog.evaluation.judges import (
    PROMPT_VERSION,
    BlockJudgment,
    EvaluationJudges,
    GlobalJudgment,
    JudgeBatchContractError,
    JudgePacket,
    SlideBatchJudgment,
    SlideJudgment,
    _call_packets,
    _global_packet_payload,
    _packet_payload,
    _render_prompt,
    _validate_batch_response,
    _validate_response,
    run_planned_evaluation,
)
from lecturelog.evaluation.openrouter import TEXT_MODEL, VISION_MODEL, JudgeResponseError
from lecturelog.evaluation.planner import RequestBudget


class Result(BaseModel):
    ok: bool


def test_prompt_version_is_v5_to_invalidate_previous_parser_cache():
    assert PROMPT_VERSION == "v5"


class FakeClient:
    def __init__(self):
        self.calls = []

    async def judge(self, **kwargs):
        self.calls.append(kwargs)
        return kwargs


@pytest.mark.asyncio
async def test_block_contract_and_russian_evidence_prompt():
    client = FakeClient()
    judges = EvaluationJudges(client)
    await judges.blocks([JudgePacket("b-1", {"text": "hello"})], Result)
    call = client.calls[0]
    assert call["model"] == TEXT_MODEL
    assert "доказательств" in call["prompt"]
    assert '"stable_id": "b-1"' in call["prompt"]


@pytest.mark.asyncio
async def test_rejects_oversized_or_duplicate_batches():
    judges = EvaluationJudges(FakeClient())
    with pytest.raises(JudgeBatchContractError, match="1..10"):
        await judges.blocks([JudgePacket(str(i), {}) for i in range(11)], Result)
    with pytest.raises(JudgeBatchContractError, match="unique"):
        await judges.sections([JudgePacket("s", {}), JudgePacket("s", {})], Result)


@pytest.mark.asyncio
async def test_slide_images_select_pinned_vision_model():
    client = FakeClient()
    judges = EvaluationJudges(client)
    await judges.slides([JudgePacket("slide-1", {})], Result, images=["data:image/png;base64,eA=="])
    assert client.calls[0]["model"] == VISION_MODEL
    assert client.calls[0]["requirement"].image_input is True


@pytest.mark.asyncio
async def test_global_is_exactly_one_packet_call():
    client = FakeClient()
    judges = EvaluationJudges(client)
    await judges.global_document(JudgePacket("document", {"findings": []}), Result)
    assert len(client.calls) == 1


@dataclass
class Cue:
    block_id: int
    start_s: float
    end_s: float
    text: str


@dataclass
class Section:
    section_id: int
    start_s: float
    end_s: float
    content_md: str = "Требования системы"


@dataclass
class Block:
    block_id: int
    section_id: int
    text: str


@dataclass
class Slide:
    slide_num: int
    native_text: str
    native_text_quality: str = "good"
    image_data_url: str | None = None


@dataclass
class Alignment:
    assignments: tuple
    placements: tuple = ()


@dataclass
class Artifacts:
    transcript: tuple
    sections: tuple
    alignment: Alignment | None = None


def test_section_and_block_packets_use_relevant_time_window():
    artifacts = Artifacts(
        transcript=(
            Cue(1, 0, 5, "не связанное вступление"),
            Cue(2, 50, 55, "обсуждаем требования системы"),
            Cue(3, 60, 65, "продолжаем требования"),
        ),
        sections=(Section(7, 45, 70),),
    )
    section_payload = _packet_payload("section", artifacts.sections[0], artifacts)
    block_payload = _packet_payload("block", Block(9, 7, "требования"), artifacts)
    assert [cue["block_id"] for cue in section_payload["transcript_evidence"]] == [2, 3]
    assert [cue["block_id"] for cue in block_payload["transcript_evidence"]] == [2, 3]


def test_section_packet_stratifies_evidence_across_full_interval():
    artifacts = Artifacts(
        transcript=tuple(
            Cue(index, index, index + 0.5, f"cue {index}") for index in range(100)
        ),
        sections=(Section(7, 0, 100),),
    )
    payload = _packet_payload("section", artifacts.sections[0], artifacts)
    ids = [cue["block_id"] for cue in payload["transcript_evidence"]]
    assert len(ids) == 30
    assert ids[0] == 0
    assert ids[-1] == 99
    assert len(set(ids)) == 30


def test_slide_packet_uses_anchor_and_excludes_it_from_lexical_alternatives():
    artifacts = Artifacts(
        transcript=(
            Cue(10, 0, 2, "введение"),
            Cue(11, 2, 4, "риски требований проекта"),
            Cue(12, 4, 6, "объясняем риски"),
            Cue(20, 40, 42, "другие риски требований"),
        ),
        sections=(),
        alignment=Alignment(
            (
                {
                    "slide_num": 3,
                    "anchor_block_id": 11,
                    "evidence_block_ids": [12],
                    "assignment_confidence": "probable",
                },
            )
        ),
    )
    packets, _ = _call_packets(
        "slide", ("3",), {"3": Slide(3, "Риски требований")}, artifacts
    )
    payload = packets[0].payload
    private_context = packets[0].validation_context
    evaluated_id = private_context["evaluated_candidate_id"]
    assert "evaluated_candidate_id" not in payload
    assert "placement_metadata" not in payload
    assert payload["transcript_evidence"] == []
    assert private_context["placement_metadata"]["anchor_block_id"] == 11
    assert all(
        {cue["block_id"] for cue in item["context"]}.isdisjoint({10, 11, 12})
        for item in payload["candidate_contexts"]
        if item["candidate_id"] != evaluated_id
    )
    assert all(
        item["candidate_id"].startswith("candidate-")
        for item in payload["candidate_contexts"]
    )
    assert private_context["system_confidence"] == "probable"
    assert {item["stable_id"] for item in payload["source_map"]} >= {
        "slide:3",
        "transcript:cue:11",
        "transcript:cue:12",
    }


def test_slide_block_index_is_resolved_inside_matching_section():
    artifacts = SimpleNamespace(
        transcript=(Cue(11, 2, 4, "риски требований проекта"),),
        sections=(
            Section(7, 0, 1, "Чужой глобальный блок"),
            Section(8, 2, 5, "Правильный локальный раздел"),
        ),
        blocks=(Block(1, 7, "не тот блок"), Block(2, 8, "правильный блок")),
        alignment=Alignment(
            assignments=({"slide_num": 3, "anchor_block_id": 11},),
            placements=(
                {
                    "slide_num": 3,
                    "global_section_id": 8,
                    "block_index": 0,
                    "output_kind": "inline",
                    "anchor_confidence": "verified",
                },
            ),
        ),
    )
    validation_context = {}
    payload = _packet_payload(
        "slide",
        Slide(3, "Риски требований"),
        artifacts,
        validation_context=validation_context,
    )
    assert "placement_metadata" not in payload
    assert validation_context["placement_metadata"]["note_context"] == {
        "section_id": 8,
        "content_md": "Правильный локальный раздел",
    }
    assert validation_context["system_confidence"] == "verified"


def test_slide_candidates_are_opaque_shuffled_and_include_decoy_and_negative():
    transcript = [Cue(1, 0, 1, "риски требований основной контекст")]
    for index in range(2, 50):
        text = (
            f"риски требований похожий контекст {index}"
            if index in {7, 13, 19, 25, 31, 37}
            else f"случайная посторонняя тема {index}"
        )
        transcript.append(Cue(index, index, index + 1, text))
    artifacts = Artifacts(
        transcript=tuple(transcript),
        sections=(),
        alignment=Alignment(({"slide_num": 3, "anchor_block_id": 1},)),
    )
    packets, _ = _call_packets(
        "slide", ("3",), {"3": Slide(3, "риски требований")}, artifacts
    )
    payload = packets[0].payload
    candidates = payload["candidate_contexts"]
    evaluated_id = packets[0].validation_context["evaluated_candidate_id"]
    assert evaluated_id != candidates[0]["candidate_id"]
    assert len(candidates) == 7  # evaluated + four top + hard decoy + random negative
    assert all(set(candidate) == {"candidate_id", "context"} for candidate in candidates)
    assert any(
        all("риски требований" not in cue["text"] for cue in candidate["context"])
        for candidate in candidates
        if candidate["candidate_id"] != evaluated_id
    )


@pytest.mark.asyncio
async def test_slide_packet_images_only_for_sparse_or_missing_native_text():
    artifacts = Artifacts(transcript=(), sections=(), alignment=None)
    sparse = Slide(
        1,
        "short",
        native_text_quality="sparse",
        image_data_url="data:image/png;base64,c3BhcnNl",
    )
    good = Slide(
        2,
        "complete native text",
        native_text_quality="good",
        image_data_url="data:image/png;base64,Z29vZA==",
    )
    packets, images = _call_packets(
        "slide",
        ("1", "2"),
        {"1": sparse, "2": good},
        artifacts,
    )
    assert images == ["data:image/png;base64,c3BhcnNl"]
    assert packets[0].payload["image_ref"] == "uploaded_image:0"
    assert "image_ref" not in packets[1].payload
    client = FakeClient()
    await EvaluationJudges(client).slides(packets, Result, images=images)
    assert client.calls[0]["model"] == VISION_MODEL
    assert client.calls[0]["images"] == images
    good_packets, good_images = _call_packets(
        "slide", ("2",), {"2": good}, artifacts
    )
    await EvaluationJudges(client).slides(
        good_packets, Result, images=good_images or None
    )
    assert client.calls[1]["model"] == TEXT_MODEL
    assert client.calls[1]["images"] is None


def test_specialized_output_schemas_expose_dimensions():
    common = {
        "stable_id": "b1",
        "score": 80,
        "confidence": 0.9,
        "evidence": [{"stable_id": "transcript:cue:1", "quote": "подтверждение"}],
        "issues": [],
    }
    block = BlockJudgment.model_validate(
        {
            **common,
            "faithfulness": 90,
            "language_consistency": 90,
            "clarity": 80,
            "local_coherence": 80,
            "heading_relevance": 80,
            "information_value": 70,
            "style_consistency": 80,
            "formatting": 100,
        }
    )
    slide = SlideJudgment.model_validate(
        {
            **common,
            "semantic_relevance": 88,
            "specificity": 77,
            "candidate_ranking": ["candidate-1", "candidate-2"],
            "anchor_precision": 72,
            "current_context_rank": 2,
            "better_context_id": "alt-20",
            "placement_verdict": "acceptable",
            "system_confidence": "verified",
            "confidence_calibration": 75,
        }
    )
    assert block.faithfulness == 90
    assert slide.placement_verdict == "acceptable"
    assert slide.current_context_rank == 2
    assert "content_coverage" in GlobalJudgment.model_json_schema()["properties"]


def test_slide_ranking_is_exact_and_private_current_metrics_are_derived():
    judgment = SlideJudgment.model_validate(
        {
            "stable_id": "3",
            "score": 80,
            "confidence": 0.8,
            "evidence": [{"stable_id": "transcript:cue:1", "quote": "context one"}],
            "issues": [],
            "semantic_relevance": 90,
            "specificity": 80,
            "candidate_ranking": ["candidate-2", "candidate-1"],
            "system_confidence": "verified",
            "confidence_calibration": 80,
        }
    )
    value = SlideBatchJudgment(judgments=[judgment])
    packet = JudgePacket(
        "3",
        {
            "candidate_contexts": [
                {"candidate_id": "candidate-1", "context": []},
                {"candidate_id": "candidate-2", "context": []},
            ],
            "source_map": [
                {"stable_id": "transcript:cue:1", "text": "context one"}
            ],
        },
        {"evaluated_candidate_id": "candidate-1"},
    )
    _validate_response(value, [packet])
    assert judgment.current_context_rank == 2
    assert judgment.better_context_id == "candidate-2"
    assert judgment.anchor_precision == 0
    assert judgment.placement_verdict == "incorrect"
    judgment.candidate_ranking = ["candidate-1", "candidate-1"]
    with pytest.raises(JudgeResponseError, match="exact unique"):
        _validate_response(value, [packet])


@pytest.mark.asyncio
async def test_runner_preserves_dimension_output_shape(tmp_path):
    common = {
        "stable_id": "1",
        "score": 80,
        "confidence": 0.9,
        "evidence": [{"stable_id": "transcript:cue:1", "quote": "требования"}],
        "issues": [],
    }

    class RunnerClient:
        budget = RequestBudget("smoke")

        async def judge(self, **kwargs):
            schema = kwargs["schema"]
            if schema.__name__ == "BlockBatchJudgment":
                payload = {
                    "judgments": [
                        {
                            **common,
                            "faithfulness": 91,
                            "language_consistency": 92,
                            "clarity": 81,
                            "local_coherence": 82,
                            "heading_relevance": 83,
                            "information_value": 84,
                            "style_consistency": 85,
                            "formatting": 86,
                        }
                    ]
                }
            else:
                payload = {
                    "faithfulness": 90,
                    "content_coverage": 80,
                    "block_quality": 85,
                    "document_structure": 75,
                    "slide_semantic_relevance": 70,
                    "slide_anchor_precision": 65,
                    "confidence_calibration": 60,
                    "confidence": 0.8,
                    "evidence": [{"stable_id": "transcript:cue:1", "quote": "требования"}],
                    "findings": [],
                }
            return SimpleNamespace(
                value=schema.model_validate(payload),
                requested_model=TEXT_MODEL,
                actual_model=TEXT_MODEL,
                cache_key="key",
                cached=True,
                prompt_tokens=0,
                completion_tokens=0,
            )

    artifacts = SimpleNamespace(
        blocks=(Block(1, 7, "требования"),),
        sections=(),
        slides=(),
        transcript=(Cue(1, 0, 5, "требования"),),
        load_findings=(),
    )
    result = await run_planned_evaluation(
        artifacts,
        "smoke",
        8,
        tmp_path,
        False,
        allow_remote=lambda: True,
        client=RunnerClient(),
    )
    assert result["blocks"][0]["faithfulness"] == 91
    assert result["global"][0]["content_coverage"] == 80
    assert result["global"][0]["findings"] == []
    assert "issues" not in result["global"][0]
    assert "judge_stability" not in result["global"][0]
    assert all(call["actual_model_reported"] is True for call in result["calls"])


@pytest.mark.asyncio
async def test_runner_preserves_attempt_records_when_batch_fails(tmp_path):
    class FailingClient:
        budget = RequestBudget("smoke")
        attempt_records = [
            {
                "requested_model": TEXT_MODEL,
                "attempt_index": 1,
                "status": "http_error",
                "http_status": 400,
                "error_stage": "transport",
                "actual_model_reported": False,
                "normalization_warnings": [],
            }
        ]

        async def judge(self, **kwargs):
            raise JudgeResponseError("bad request")

    artifacts = SimpleNamespace(
        blocks=(Block(1, 7, "требования"),),
        sections=(),
        slides=(),
        transcript=(Cue(1, 0, 5, "требования"),),
        load_findings=(),
    )
    result = await run_planned_evaluation(
        artifacts,
        "smoke",
        8,
        tmp_path,
        False,
        allow_remote=lambda: True,
        client=FailingClient(),
    )
    assert result["incomplete"] is True
    assert result["attempts"][0]["status"] == "http_error"


def test_batch_rejects_short_or_missing_ids_and_reorders_complete_response():
    base = {
        "stable_id": "a",
        "score": 50,
        "confidence": 0.5,
        "evidence": [{"stable_id": "transcript:cue:1", "quote": "evidence"}],
        "issues": [],
        "faithfulness": 50,
        "language_consistency": 50,
        "clarity": 50,
        "local_coherence": 50,
        "heading_relevance": 50,
        "information_value": 50,
        "style_consistency": 50,
        "formatting": 50,
    }
    value = SimpleNamespace(judgments=[BlockJudgment.model_validate(base)])
    packets = [JudgePacket("a", {}), JudgePacket("b", {})]
    with pytest.raises(JudgeResponseError, match="exact unique IDs"):
        _validate_batch_response(value, packets)
    reordered = SimpleNamespace(
        judgments=[
            BlockJudgment.model_validate({**base, "stable_id": "b"}),
            BlockJudgment.model_validate(base),
        ]
    )
    _validate_batch_response(reordered, packets)
    assert [judgment.stable_id for judgment in reordered.judgments] == ["a", "b"]


def test_batch_rejects_duplicate_ids_even_when_count_matches():
    base = {
        "stable_id": "a",
        "score": 50,
        "confidence": 0.5,
        "evidence": [{"stable_id": "transcript:cue:1", "quote": "evidence"}],
        "issues": [],
        "faithfulness": 50,
        "language_consistency": 50,
        "clarity": 50,
        "local_coherence": 50,
        "heading_relevance": 50,
        "information_value": 50,
        "style_consistency": 50,
        "formatting": 50,
    }
    duplicate = SimpleNamespace(
        judgments=[
            BlockJudgment.model_validate(base),
            BlockJudgment.model_validate(base),
        ]
    )
    with pytest.raises(JudgeResponseError, match="exact unique IDs"):
        _validate_batch_response(
            duplicate,
            [JudgePacket("a", {}), JudgePacket("b", {})],
        )


def test_item_and_global_reject_empty_evidence():
    common = {
        "stable_id": "a",
        "score": 0,
        "confidence": 0,
        "evidence": [],
        "issues": [],
        "faithfulness": 0,
        "language_consistency": 0,
        "clarity": 0,
        "local_coherence": 0,
        "heading_relevance": 0,
        "information_value": 0,
        "style_consistency": 0,
        "formatting": 0,
    }
    with pytest.raises(ValidationError, match="requires direct evidence"):
        BlockJudgment.model_validate(common)
    with pytest.raises(ValidationError):
        GlobalJudgment.model_validate(
            {
                "faithfulness": 0,
                "content_coverage": 0,
                "block_quality": 0,
                "document_structure": 0,
                "slide_semantic_relevance": 0,
                "slide_anchor_precision": 0,
                "confidence_calibration": 0,
                "confidence": 0,
                "evidence": [],
                "findings": [],
            }
        )


def test_block_faithfulness_rejects_note_self_citation():
    payload = {
        "stable_id": "1",
        "score": 80,
        "confidence": 0.8,
        "evidence": [{"stable_id": "note:block:1", "quote": "готовый конспект"}],
        "issues": [],
        "faithfulness": 80,
        "language_consistency": 80,
        "clarity": 80,
        "local_coherence": 80,
        "heading_relevance": 80,
        "information_value": 80,
        "style_consistency": 80,
        "formatting": 80,
    }
    with pytest.raises(ValidationError, match="requires transcript evidence"):
        BlockJudgment.model_validate(payload)


def test_evidence_validation_rejects_unknown_id_and_quote_mismatch():
    judgment = BlockJudgment.model_validate(
        {
            "stable_id": "1",
            "score": 80,
            "confidence": 0.8,
            "evidence": [
                {"stable_id": "transcript:cue:1", "quote": "подтверждение"}
            ],
            "issues": [],
            "faithfulness": 80,
            "language_consistency": 80,
            "clarity": 80,
            "local_coherence": 80,
            "heading_relevance": 80,
            "information_value": 80,
            "style_consistency": 80,
            "formatting": 80,
        }
    )
    value = SimpleNamespace(judgments=[judgment])
    with pytest.raises(JudgeResponseError, match="Unknown evidence ID"):
        _validate_response(value, [JudgePacket("1", {"source_map": []})])
    with pytest.raises(JudgeResponseError, match="does not match"):
        _validate_response(
            value,
            [
                JudgePacket(
                    "1",
                    {
                        "source_map": [
                            {
                                "stable_id": "transcript:cue:1",
                                "text": "совершенно другой фрагмент",
                            }
                        ]
                    },
                )
            ],
        )


def test_evidence_source_namespace_cannot_collide_between_packets():
    value = SimpleNamespace(evidence=[], findings=[])
    with pytest.raises(JudgeResponseError, match="namespace collision"):
        _validate_response(
            value,
            [
                JudgePacket(
                    "1",
                    {
                        "source_map": [
                            {"stable_id": "transcript:cue:1", "text": "первый текст"}
                        ]
                    },
                ),
                JudgePacket(
                    "2",
                    {
                        "source_map": [
                            {"stable_id": "transcript:cue:1", "text": "другой текст"}
                        ]
                    },
                ),
            ],
        )


def test_issue_missing_id_with_own_evidence_derives_parent_id_and_is_validated():
    judgment = BlockJudgment.model_validate(
        {
            "stable_id": "1",
            "score": 60,
            "confidence": 0.6,
            "evidence": [{"stable_id": "transcript:cue:1", "quote": "исходная цитата"}],
            "issues": [
                {
                    "kind": "clarity",
                    "code": "unclear_wording",
                    "severity": "warning",
                    "message": "Неясная формулировка",
                    "evidence": [
                        {
                            "stable_id": "transcript:cue:1",
                            "quote": "исходная цитата",
                        }
                    ],
                }
            ],
            "faithfulness": 70,
            "language_consistency": 80,
            "clarity": 40,
            "local_coherence": 60,
            "heading_relevance": 70,
            "information_value": 60,
            "style_consistency": 70,
            "formatting": 80,
        }
    )
    assert judgment.issues[0].stable_id == judgment.stable_id
    value = SimpleNamespace(judgments=[judgment])
    _validate_response(
        value,
        [
            JudgePacket(
                "1",
                {
                    "source_map": [
                        {
                            "stable_id": "transcript:cue:1",
                            "text": "Здесь есть исходная цитата лектора.",
                        }
                    ]
                },
            )
        ],
    )
    with pytest.raises(JudgeResponseError, match="Unknown evidence ID"):
        _validate_response(value, [JudgePacket("1", {"source_map": []})])
    with pytest.raises(JudgeResponseError, match="does not match"):
        _validate_response(
            value,
            [
                JudgePacket(
                    "1",
                    {
                        "source_map": [
                            {
                                "stable_id": "transcript:cue:1",
                                "text": "цитата отсутствует",
                            }
                        ]
                    },
                )
            ],
        )


def test_issue_without_own_or_parent_evidence_is_rejected():
    with pytest.raises(ValidationError):
        BlockJudgment.model_validate(
            {
                "stable_id": "1",
                "score": 0,
                "confidence": 0,
                "evidence": [],
                "issues": [
                    {
                        "kind": "insufficient_evidence",
                        "code": "missing_source",
                        "severity": "major",
                        "message": "Нет источника",
                    }
                ],
                "faithfulness": 0,
                "language_consistency": 0,
                "clarity": 0,
                "local_coherence": 0,
                "heading_relevance": 0,
                "information_value": 0,
                "style_consistency": 0,
                "formatting": 0,
            }
        )


def test_global_finding_missing_id_derives_own_evidence_id():
    judgment = GlobalJudgment.model_validate(
        {
            "faithfulness": 70,
            "content_coverage": 70,
            "block_quality": 70,
            "document_structure": 70,
            "slide_semantic_relevance": 70,
            "slide_anchor_precision": 70,
            "confidence_calibration": 70,
            "evidence": [{"stable_id": "transcript:cue:1", "quote": "цитата"}],
            "findings": [
                {
                    "kind": "document_structure",
                    "code": "fragmented_document",
                    "severity": "warning",
                    "message": "Документ фрагментирован",
                    "evidence": [
                        {"stable_id": "transcript:cue:1", "quote": "цитата"}
                    ],
                }
            ],
        }
    )
    assert judgment.findings[0].stable_id == "transcript:cue:1"


def test_global_finding_without_own_or_parent_evidence_is_rejected():
    with pytest.raises(ValidationError):
        GlobalJudgment.model_validate(
            {
                "faithfulness": 0,
                "content_coverage": 0,
                "block_quality": 0,
                "document_structure": 0,
                "slide_semantic_relevance": 0,
                "slide_anchor_precision": 0,
                "confidence_calibration": 0,
                "evidence": [],
                "findings": [
                    {
                        "kind": "insufficient_evidence",
                        "code": "missing_source",
                        "severity": "major",
                        "message": "Нет источника",
                    }
                ],
            }
        )


def test_global_packet_contains_only_referenced_sources_and_structured_findings():
    artifacts = SimpleNamespace(
        blocks=(),
        sections=(),
        slides=(),
        transcript=tuple(
            Cue(index, index, index + 1, f"cue text {index} " + "x" * 500)
            for index in range(1, 101)
        ),
    )
    packet = _global_packet_payload(
        artifacts,
        [
            {
                "stable_id": "block-1",
                "evidence": [
                    {"stable_id": "transcript:cue:2", "quote": "cue text 2"}
                ],
                "issues": [],
            }
        ],
        [
            {
                "code": "broken_link",
                "severity": "major",
                "message": "Broken Markdown link",
                "evidence": ["missing.png"],
            }
        ],
    )
    source_ids = {source["stable_id"] for source in packet["source_map"]}
    assert source_ids == {"transcript:cue:2", "finding:1"}
    assert packet["deterministic_findings"][0]["stable_id"] == "finding:1"
    assert "transcript:cue:1" not in source_ids
    prompt = _render_prompt("global", [JudgePacket("document", packet)])
    assert len(prompt) < 10_000
