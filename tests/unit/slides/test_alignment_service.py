import json

import pytest

from lecturelog.domain.slides import SlideAsset
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
    (prompts / "document_slide_catalog_v1.md").write_text("catalog")
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
    service = DocumentAlignmentService(
        llm=llm, models=["m"], prompts_dir=prompts, effort="low"
    )
    result = await service.align(
        assets=[SlideAsset(1, image, "document", extracted_text="", native_text_quality="none")],
        section_layout=_layout(),
        srt_content=_srt(),
    )
    assert result[0].match_status == "discussed"
    assert len(llm.calls) == 2
    assert llm.calls[0]["images"]
    assert llm.calls[1]["response_json"] is True


@pytest.mark.asyncio
async def test_deck_guard_marks_unrelated_deck(tmp_path):
    image = tmp_path / "slide.png"
    image.write_bytes(b"not-a-real-image")
    service = DocumentAlignmentService()
    result = await service.align(
        assets=[
            SlideAsset(
                1, image, "document",
                extracted_text="Совершенно посторонняя квантовая химия",
                native_text_quality="good",
            )
        ],
        section_layout=_layout(),
        srt_content=_srt(),
    )
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
                SlideAsset(
                    1, image, "document", extracted_text="x", native_text_quality="sparse"
                )
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
    blocks = parse_srt_blocks(
        "1\n00:00:00,000 --> 00:00:30,000\ntext\n"
    )
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
