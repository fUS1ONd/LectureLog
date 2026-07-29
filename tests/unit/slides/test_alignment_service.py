import json

import pytest

from lecturelog.domain.slides import (
    SectionRef,
    SlideAsset,
    SlideAssignment,
    SlideCatalogEntry,
)
from lecturelog.infrastructure.slides.alignment.service import DocumentAlignmentService
from lecturelog.infrastructure.srt import parse_srt_blocks


class ScriptedLlm:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def call(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


def _srt(text="Обсуждаем бинарное дерево поиска"):
    return f"1\n00:00:00,000 --> 00:00:10,000\n{text}\n"


def _layout():
    return [[{"title": "Деревья", "start": "0:00", "end": "0:10"}]]


@pytest.mark.asyncio
async def test_llm_catalog_and_semantic_verification_are_used(tmp_path):
    image = tmp_path / "slide.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nimage")
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "document_slide_catalog_v3.md").write_text("catalog")
    (prompts / "document_slide_semantic_match_v1.md").write_text("semantic")
    llm = ScriptedLlm(
        [
            json.dumps(
                {
                    "slides": [
                        {
                            "slide_num": 1,
                            "role": "content",
                            "title": "Бинарное дерево",
                            "visible_text": "Бинарное дерево поиска",
                            "source_concepts": ["дерево поиска"],
                            "transcript_language_terms": [],
                            "visual_summary": "",
                            "formulas": [],
                        }
                    ]
                }
            ),
            json.dumps(
                {
                    "slide_num": 1,
                    "global_section_id": 0,
                    "evidence_block_ids": [1],
                    "evidence_quote": "Обсуждаем бинарное дерево поиска",
                    "semantic_tier": "explicit",
                }
            ),
        ]
    )
    service = DocumentAlignmentService(llm=llm, models=["m"], prompts_dir=prompts, effort="low")
    result = (
        await service.align(
            assets=[
                SlideAsset(1, image, "document", extracted_text="", native_text_quality="none")
            ],
            section_layout=_layout(),
            srt_content=_srt(),
        )
    ).assignments
    assert result[0].match_status == "discussed"
    assert len(llm.calls) == 2
    assert llm.calls[0]["images"]
    assert llm.calls[1]["response_json"] is True
    assert llm.calls[0]["temperature"] == 0
    assert llm.calls[1]["temperature"] == 0


@pytest.mark.asyncio
async def test_deck_guard_marks_unrelated_deck(tmp_path):
    image = tmp_path / "slide.png"
    image.write_bytes(b"not-a-real-image")
    service = DocumentAlignmentService()
    result = (
        await service.align(
            assets=[
                SlideAsset(
                    1,
                    image,
                    "document",
                    extracted_text="Совершенно посторонняя квантовая химия",
                    native_text_quality="good",
                )
            ],
            section_layout=_layout(),
            srt_content=_srt(),
        )
    ).assignments
    assert result[0].match_status == "deck_mismatch"
    assert result[0].reason_code.startswith("deck_guard")


@pytest.mark.asyncio
async def test_invalid_section_timeline_fails_closed(tmp_path):
    image = tmp_path / "slide.png"
    image.write_bytes(b"x")
    service = DocumentAlignmentService()
    with pytest.raises(ValueError, match="timeline"):
        await service.align(
            assets=[
                SlideAsset(1, image, "document", extracted_text="x", native_text_quality="sparse")
            ],
            section_layout=[
                [
                    {"title": "later", "start": "0:05", "end": "0:10"},
                    {"title": "earlier", "start": "0:02", "end": "0:04"},
                ]
            ],
            srt_content=_srt(),
        )


def test_overlapping_boundary_is_clamped_when_timeline_advances():
    blocks = parse_srt_blocks("1\n00:00:00,000 --> 00:00:30,000\ntext\n")
    refs = DocumentAlignmentService._section_refs(
        [
            [
                {"title": "one", "start": "0:00", "end": "0:12"},
                {"title": "two", "start": "0:10", "end": "0:20"},
            ]
        ],
        blocks,
    )

    assert refs[0].start_s == 0
    assert refs[0].end_s == 12
    assert refs[1].start_s == 12
    assert refs[1].end_s == 20


def test_global_recovery_finds_distinctive_term_outside_local_pool():
    blocks = parse_srt_blocks(
        "1\n00:00:00,000 --> 00:00:05,000\n"
        "Обсуждаем software engineering\n\n"
        "2\n00:00:05,000 --> 00:00:10,000\nТеперь разберём SWEBOK\n"
    )
    sections = (
        SectionRef(0, 0, 0, 0, 4.9),
        SectionRef(1, 0, 1, 5, 10),
    )
    entry = SlideCatalogEntry(
        1,
        "content",
        "SWEBOK",
        "SWEBOK Software Engineering Book of Knowledge",
        ("software engineering",),
    )

    recovered = DocumentAlignmentService()._global_recovery(entry, sections, blocks)

    assert len(recovered) == 1
    assert recovered[0].global_section_id == 1
    assert recovered[0].semantic_tier == "strong"


def test_global_recovery_rejects_generic_single_word_overlap():
    blocks = parse_srt_blocks("1\n00:00:00,000 --> 00:00:05,000\nОбсуждаем требования системы\n")
    sections = (SectionRef(0, 0, 0, 0, 5),)
    entry = SlideCatalogEntry(
        1,
        "content",
        "Метаданные требований",
        "Автор Ревизия Состояние Источник",
    )

    assert DocumentAlignmentService()._global_recovery(entry, sections, blocks) == ()


def test_evidence_collision_downgrades_unrelated_verified_assignments():
    assignments = tuple(
        SlideAssignment(
            slide_num,
            "discussed",
            slide_num,
            (42,),
            float(slide_num),
            "verified",
            10.0,
            "matched",
        )
        for slide_num in (1, 2, 3)
    )

    result = DocumentAlignmentService._downgrade_evidence_collisions(assignments, ())

    assert {item.assignment_confidence for item in result} == {"probable"}
    assert all(item.reason_code.endswith(":evidence_collision") for item in result)


@pytest.mark.asyncio
async def test_navigation_role_requires_semantic_evidence(tmp_path):
    image = tmp_path / "slide.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nimage")
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "document_slide_catalog_v3.md").write_text("catalog")
    (prompts / "document_slide_semantic_match_v1.md").write_text("semantic")
    llm = ScriptedLlm(
        [
            json.dumps(
                {
                    "slides": [
                        {
                            "slide_num": 1,
                            "role": "title",
                            "title": "Бинарное дерево",
                            "visible_text": "Бинарное дерево поиска",
                            "source_concepts": ["дерево поиска"],
                            "transcript_language_terms": [],
                            "visual_summary": "",
                            "formulas": [],
                        }
                    ]
                }
            ),
            json.dumps(
                {
                    "slide_num": 1,
                    "global_section_id": 0,
                    "evidence_block_ids": [1],
                    "evidence_quote": "Обсуждаем бинарное дерево поиска",
                    "semantic_tier": "explicit",
                }
            ),
        ]
    )
    service = DocumentAlignmentService(llm=llm, models=["m"], prompts_dir=prompts, effort="low")

    result = (
        await service.align(
            assets=[
                SlideAsset(1, image, "document", extracted_text="", native_text_quality="none")
            ],
            section_layout=_layout(),
            srt_content=_srt(),
        )
    ).assignments

    assert result[0].match_status == "discussed"
    assert result[0].global_section_id == 0
    assert result[0].assignment_confidence == "probable"


@pytest.mark.asyncio
async def test_blank_role_remains_unmentioned(tmp_path):
    image = tmp_path / "slide.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nimage")
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "document_slide_catalog_v3.md").write_text("catalog")
    llm = ScriptedLlm(
        [
            json.dumps(
                {
                    "slides": [
                        {
                            "slide_num": 1,
                            "role": "blank",
                            "title": None,
                            "visible_text": "",
                        }
                    ]
                }
            )
        ]
    )
    service = DocumentAlignmentService(llm=llm, models=["m"], prompts_dir=prompts)

    result = (
        await service.align(
            assets=[
                SlideAsset(1, image, "document", extracted_text="", native_text_quality="none")
            ],
            section_layout=_layout(),
            srt_content=_srt(),
        )
    ).assignments

    assert result[0].match_status == "unmentioned"
    assert result[0].reason_code == "service_role:blank"


@pytest.mark.asyncio
async def test_native_catalog_filters_deck_wide_header(tmp_path):
    """Без LLM каталог строится нативно — колонтитул колоды не должен попасть в claim слайда."""
    header = "Разработка программного обеспечения"
    assets = []
    for number, body in enumerate(["Лекция 1", "Организационное", "Жизненный цикл"], start=1):
        path = tmp_path / f"{number}.png"
        path.write_bytes(b"\x89PNG\r\n\x1a\nimage")
        assets.append(
            SlideAsset(
                number,
                path,
                "document",
                extracted_text=f"{header}\n{body}",
                native_text_quality="good",
            )
        )
    service = DocumentAlignmentService()

    entries, _verified = await service._catalog(assets, None)

    assert [entry.title for entry in entries.values()] == [
        "Лекция 1",
        "Организационное",
        "Жизненный цикл",
    ]
    assert all(header not in entry.visible_text for entry in entries.values())


@pytest.mark.asyncio
async def test_catalog_does_not_lower_output_ceiling(tmp_path):
    """Каталог не занижает потолок ответа — иначе JSON снова начнёт обрезаться."""
    image = tmp_path / "slide.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nimage")
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "document_slide_catalog_v3.md").write_text("catalog")
    llm = ScriptedLlm(
        [
            json.dumps(
                {
                    "slides": [
                        {
                            "slide_num": 1,
                            "role": "content",
                            "title": "Бинарное дерево",
                            "visible_text": "Бинарное дерево поиска",
                        }
                    ]
                }
            )
        ]
    )
    service = DocumentAlignmentService(llm=llm, models=["m"], prompts_dir=prompts, effort="low")

    await service._catalog(
        [SlideAsset(1, image, "document", extracted_text="", native_text_quality="none")], None
    )

    assert llm.calls[0].get("max_tokens") is None


@pytest.mark.asyncio
async def test_catalog_repairs_invalid_schema_once(tmp_path):
    """Сорванная схема не должна молча терять весь batch — сначала одна попытка починки."""
    image = tmp_path / "slide.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nimage")
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "document_slide_catalog_v3.md").write_text("catalog")
    broken = json.dumps({"slides": [{"role": "content", "title": "без slide_num"}]})
    valid = json.dumps(
        {
            "slides": [
                {
                    "slide_num": 1,
                    "role": "content",
                    "title": "Бинарное дерево",
                    "visible_text": "Бинарное дерево поиска",
                }
            ]
        }
    )
    llm = ScriptedLlm([broken, valid])
    service = DocumentAlignmentService(llm=llm, models=["m"], prompts_dir=prompts, effort="low")

    entries, verified = await service._catalog(
        [
            SlideAsset(
                1, image, "document", extracted_text="запасной текст", native_text_quality="good"
            )
        ],
        None,
    )

    assert len(llm.calls) == 2
    assert "slide_num" in llm.calls[1]["prompt"]
    assert entries[1].title == "Бинарное дерево"
    assert verified == {1}


@pytest.mark.asyncio
async def test_catalog_falls_back_after_single_failed_repair(tmp_path):
    """Починка одна: если и она сорвалась, уходим в native text, а не крутим запросы."""
    image = tmp_path / "slide.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nimage")
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "document_slide_catalog_v3.md").write_text("catalog")
    broken = json.dumps({"slides": [{"role": "content", "title": "без slide_num"}]})
    llm = ScriptedLlm([broken, broken])
    service = DocumentAlignmentService(llm=llm, models=["m"], prompts_dir=prompts, effort="low")

    entries, verified = await service._catalog(
        [
            SlideAsset(
                1, image, "document", extracted_text="запасной текст", native_text_quality="good"
            )
        ],
        None,
    )

    assert len(llm.calls) == 2
    assert entries[1].title == "запасной текст"
    assert verified == set()


@pytest.mark.asyncio
async def test_catalog_and_semantic_calls_use_strict_schema(tmp_path):
    """Оба структурированных вызова матчера должны идти со схемой, а не с json_object."""
    image = tmp_path / "slide.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nimage")
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "document_slide_catalog_v3.md").write_text("catalog")
    (prompts / "document_slide_semantic_match_v1.md").write_text("semantic")
    llm = ScriptedLlm(
        [
            json.dumps(
                {
                    "slides": [
                        {
                            "slide_num": 1,
                            "role": "content",
                            "title": "Бинарное дерево",
                            "visible_text": "Бинарное дерево поиска",
                            "source_concepts": ["дерево поиска"],
                        }
                    ]
                }
            ),
            json.dumps(
                {
                    "slide_num": 1,
                    "global_section_id": 0,
                    "evidence_block_ids": [1],
                    "evidence_quote": "Обсуждаем бинарное дерево поиска",
                    "semantic_tier": "explicit",
                }
            ),
        ]
    )
    service = DocumentAlignmentService(llm=llm, models=["m"], prompts_dir=prompts, effort="low")

    await service.align(
        assets=[SlideAsset(1, image, "document", extracted_text="", native_text_quality="none")],
        section_layout=_layout(),
        srt_content=_srt(),
    )

    catalog_schema = llm.calls[0]["response_schema"]
    semantic_schema = llm.calls[1]["response_schema"]
    assert "slides" in catalog_schema["properties"]
    assert set(semantic_schema["required"]) == set(semantic_schema["properties"])


@pytest.mark.asyncio
async def test_align_returns_catalog_for_render_context(tmp_path):
    """Каталог нужен рендеру: в нём имена собственные в правильном написании."""
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "document_slide_catalog_v3.md").write_text("catalog")
    (prompts / "document_slide_semantic_match_v1.md").write_text("semantic")
    image = tmp_path / "slide.png"
    image.write_bytes(b"\x89PNGfake")
    llm = ScriptedLlm(
        [
            json.dumps(
                {
                    "slides": [
                        {
                            "slide_num": 1,
                            "role": "content",
                            "title": "ENIAC",
                            "visible_text": "первая ЭВМ",
                            "source_concepts": ["ENIAC"],
                            "transcript_language_terms": ["ЭНИАК"],
                            "visual_summary": "",
                            "formulas": [],
                        }
                    ]
                }
            ),
            json.dumps(
                {
                    "slide_num": 1,
                    "global_section_id": 0,
                    "evidence_block_ids": [1],
                    "evidence_quote": "Обсуждаем бинарное дерево поиска",
                    "semantic_tier": "explicit",
                }
            ),
        ]
    )
    service = DocumentAlignmentService(llm=llm, models=["m"], prompts_dir=prompts, effort="low")

    result = await service.align(
        assets=[SlideAsset(1, image, "document", extracted_text="", native_text_quality="none")],
        section_layout=_layout(),
        srt_content=_srt(),
    )

    assert result.catalog[1].title == "ENIAC"
    assert result.assignments[0].slide_num == 1
