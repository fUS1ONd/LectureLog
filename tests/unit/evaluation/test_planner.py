from dataclasses import dataclass

import pytest

from lecturelog.evaluation.planner import (
    EvaluationProfile,
    RequestBudget,
    RequestBudgetExceeded,
    plan_judge_batches,
)


@dataclass
class Block:
    block_id: str
    kind: str = "paragraph"
    text: str = "содержательный блок " * 10
    heading_path: tuple[str, ...] = ()


@dataclass
class Section:
    section_id: str


@dataclass
class Slide:
    slide_number: int


def test_static_has_no_remote_calls():
    assert plan_judge_batches("static", blocks=[Block("b1")]) == []
    assert RequestBudget("static").limit == 0


def test_smoke_is_batched_and_bounded():
    plan = plan_judge_batches(
        "smoke",
        blocks=[Block(f"b{i}") for i in range(20)],
        sections=[Section(f"s{i}") for i in range(10)],
        slides=[Slide(i) for i in range(10)],
    )
    assert [(call.kind, len(call.item_ids)) for call in plan] == [
        ("block", 4),
        ("section", 4),
        ("slide", 2),
        ("global", 1),
    ]
    assert len(plan) == 4
    assert plan.metadata.logical_requests == 4
    assert plan.metadata.worst_case_physical_requests == 8


def test_user_cap_can_only_lower_profile_hard_cap():
    assert RequestBudget(EvaluationProfile.STANDARD, max_requests=100).limit == 24
    budget = RequestBudget("standard", max_requests=1)
    budget.consume()
    with pytest.raises(RequestBudgetExceeded, match="resume"):
        budget.consume()


def test_smoke_content_sampling_is_stratified_and_excludes_non_content():
    blocks = [
        Block("toc", "heading", "Оглавление"),
        *[Block(str(index)) for index in range(30)],
        Block("code", "code", "print('x')"),
    ]
    plan = plan_judge_batches("smoke", blocks=blocks)
    sampled = next(batch.item_ids for batch in plan if batch.kind == "block")
    numeric = [int(value) for value in sampled]
    assert len(sampled) == 4
    assert "toc" not in sampled and "code" not in sampled
    assert min(numeric) == 0
    assert max(numeric) == 29
    assert len(set(numeric)) == 4


def test_standard_caps_items_and_avoids_first_n_section_bias():
    plan = plan_judge_batches(
        "standard",
        blocks=[Block(str(index)) for index in range(100)],
        sections=[Section(str(index)) for index in range(30)],
        slides=[Slide(index) for index in range(40)],
    )
    def ids(kind):
        return [value for batch in plan if batch.kind == kind for value in batch.item_ids]
    assert len(ids("block")) == 24
    assert len(ids("section")) == 10
    assert len(ids("slide")) == 12
    assert ids("section")[-1] == "29"
    assert [len(batch.item_ids) for batch in plan if batch.kind == "slide"] == [3, 3, 3, 3]


def test_deep_keeps_four_slide_batch_size():
    plan = plan_judge_batches("deep", slides=[Slide(index) for index in range(10)])
    assert [len(batch.item_ids) for batch in plan if batch.kind == "slide"] == [4, 4, 2]


def test_deep_covers_every_section_and_slide_and_exposes_coverage_metadata():
    plan = plan_judge_batches(
        "deep",
        sections=[Section(str(index)) for index in range(27)],
        slides=[Slide(index) for index in range(31)],
    )
    assert len([item for batch in plan if batch.kind == "section" for item in batch.item_ids]) == 27
    assert len([item for batch in plan if batch.kind == "slide" for item in batch.item_ids]) == 31
    coverage = {item.kind: item for item in plan.metadata.coverage}
    assert coverage["section"].exhaustive
    assert coverage["slide"].exhaustive
    assert not plan.metadata.release_capable
    assert "stability repeats" in plan.metadata.release_incapable_reasons[-1]


def test_sampled_profiles_are_explicitly_directional_and_estimate_retries():
    plan = plan_judge_batches(
        "standard",
        blocks=[Block(str(index)) for index in range(100)],
        sections=[Section(str(index)) for index in range(30)],
        slides=[Slide(index) for index in range(40)],
    )
    assert {item.mode for item in plan.metadata.coverage} == {"sampled_directional"}
    assert plan.metadata.assumed_cache_misses == len(plan)
    assert plan.metadata.worst_case_physical_requests == 24
    assert not plan.metadata.release_capable


def test_deep_marks_cap_insufficient_instead_of_claiming_exhaustive_coverage():
    plan = plan_judge_batches("deep", slides=[Slide(index) for index in range(300)])

    assert len(plan) == 45
    slide_coverage = next(item for item in plan.metadata.coverage if item.kind == "slide")
    assert slide_coverage.planned < slide_coverage.total
    assert not slide_coverage.exhaustive
    assert not plan.metadata.release_capable
    assert any(
        "slide coverage is incomplete" in reason
        for reason in plan.metadata.release_incapable_reasons
    )


def test_suspicious_slides_are_prioritized_before_stratified_sample():
    alignment = type(
        "Alignment",
        (),
        {
            "assignments": (
                {
                    "slide_num": 9,
                    "score": 0.1,
                    "global_section_id": 2,
                    "evidence_block_ids": [99],
                },
                {
                    "slide_num": 10,
                    "score": 0.2,
                    "global_section_id": 2,
                    "evidence_block_ids": [99],
                },
            )
        },
    )()
    plan = plan_judge_batches(
        "smoke", slides=[Slide(index) for index in range(1, 16)], alignment=alignment
    )
    sampled = next(batch.item_ids for batch in plan if batch.kind == "slide")
    assert sampled[:2] == ("9", "10")


def test_sampling_excludes_toc_heading_and_generated_wikilink_list():
    blocks = [
        Block("toc-body", text="Описание пункта " * 10, heading_path=("Оглавление",)),
        Block(
            "toc-list",
            kind="list",
            text="- [[Раздел один]]\n- [[Раздел два]]\n- [[Раздел три]]",
        ),
        *[Block(str(index)) for index in range(10)],
    ]
    plan = plan_judge_batches("smoke", blocks=blocks)
    sampled = next(batch.item_ids for batch in plan if batch.kind == "block")
    assert "toc-body" not in sampled
    assert "toc-list" not in sampled
