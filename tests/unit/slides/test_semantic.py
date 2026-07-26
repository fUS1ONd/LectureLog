import json

import pytest

from lecturelog.domain.slides import SectionRef, SlideCatalogEntry
from lecturelog.infrastructure.slides.alignment.retrieval import generate_candidates
from lecturelog.infrastructure.slides.alignment.semantic import validate_semantic_response
from lecturelog.infrastructure.srt import parse_srt_blocks


def test_semantic_rejects_fake_evidence_id_and_ungrounded_quote() -> None:
    blocks = parse_srt_blocks("1\n00:00:00,000 --> 00:00:05,000\nобсудим графы\n")
    entry = SlideCatalogEntry(1, "content", "Графы", "графы")
    candidates = generate_candidates(entry, (SectionRef(0, 0, 0, 0, 5),), blocks)
    with pytest.raises(ValueError):
        validate_semantic_response(
            json.dumps(
                {
                    "slide_num": 1,
                    "global_section_id": 0,
                    "evidence_block_ids": [99],
                    "evidence_quote": "графы",
                    "semantic_tier": "explicit",
                }
            ),
            entry=entry,
            candidates=candidates,
            blocks=blocks,
        )


def test_semantic_rejects_explicit_match_based_on_generic_token_only() -> None:
    blocks = parse_srt_blocks(
        "1\n00:00:00,000 --> 00:00:05,000\nЭта модель просто фигачит дальше\n"
    )
    entry = SlideCatalogEntry(
        1,
        "visual_example",
        "Спиральная модель",
        "Управление рисками на каждом витке спирали",
        ("итерация с анализом рисков",),
    )
    candidates = generate_candidates(entry, (SectionRef(0, 0, 0, 0, 5),), blocks)

    with pytest.raises(ValueError, match="не подтверждает"):
        validate_semantic_response(
            json.dumps(
                {
                    "slide_num": 1,
                    "global_section_id": 0,
                    "evidence_block_ids": [1],
                    "evidence_quote": "Эта модель просто фигачит дальше",
                    "semantic_tier": "explicit",
                }
            ),
            entry=entry,
            candidates=candidates,
            blocks=blocks,
        )


def test_semantic_accepts_single_distinctive_term() -> None:
    blocks = parse_srt_blocks(
        "1\n00:00:00,000 --> 00:00:05,000\nТеперь разберём SWEBOK\n"
    )
    entry = SlideCatalogEntry(1, "content", "SWEBOK", "SWEBOK")
    candidates = generate_candidates(entry, (SectionRef(0, 0, 0, 0, 5),), blocks)

    result = validate_semantic_response(
        json.dumps(
            {
                "slide_num": 1,
                "global_section_id": 0,
                "evidence_block_ids": [1],
                "evidence_quote": "Теперь разберём SWEBOK",
                "semantic_tier": "explicit",
            }
        ),
        entry=entry,
        candidates=candidates,
        blocks=blocks,
    )

    assert result is not None


def test_semantic_rejects_exact_generic_single_word_claim() -> None:
    blocks = parse_srt_blocks(
        "1\n00:00:00,000 --> 00:00:05,000\nТеперь обсудим систему\n"
    )
    entry = SlideCatalogEntry(1, "content", "Система", "Система")
    candidates = generate_candidates(entry, (SectionRef(0, 0, 0, 0, 5),), blocks)

    with pytest.raises(ValueError, match="не подтверждает"):
        validate_semantic_response(
            json.dumps(
                {
                    "slide_num": 1,
                    "global_section_id": 0,
                    "evidence_block_ids": [1],
                    "evidence_quote": "Теперь обсудим систему",
                    "semantic_tier": "explicit",
                }
            ),
            entry=entry,
            candidates=candidates,
            blocks=blocks,
        )


def test_semantic_accepts_single_item_array_transport_shape() -> None:
    blocks = parse_srt_blocks(
        "1\n00:00:00,000 --> 00:00:05,000\nОбсуждаем бинарное дерево поиска\n"
    )
    entry = SlideCatalogEntry(
        1, "content", "Бинарное дерево", "Бинарное дерево поиска"
    )
    candidates = generate_candidates(entry, (SectionRef(0, 0, 0, 0, 5),), blocks)

    result = validate_semantic_response(
        json.dumps(
            [
                {
                    "slide_num": 1,
                    "global_section_id": 0,
                    "evidence_block_ids": [1],
                    "evidence_quote": "Обсуждаем бинарное дерево поиска",
                    "semantic_tier": "explicit",
                }
            ]
        ),
        entry=entry,
        candidates=candidates,
        blocks=blocks,
    )

    assert result is not None


def test_semantic_rejects_multi_item_array() -> None:
    blocks = parse_srt_blocks(
        "1\n00:00:00,000 --> 00:00:05,000\nОбсуждаем бинарное дерево поиска\n"
    )
    entry = SlideCatalogEntry(
        1, "content", "Бинарное дерево", "Бинарное дерево поиска"
    )
    candidates = generate_candidates(entry, (SectionRef(0, 0, 0, 0, 5),), blocks)
    item = {
        "slide_num": 1,
        "global_section_id": 0,
        "evidence_block_ids": [1],
        "evidence_quote": "Обсуждаем бинарное дерево поиска",
        "semantic_tier": "explicit",
    }

    with pytest.raises(ValueError, match="ровно один"):
        validate_semantic_response(
            json.dumps([item, item]),
            entry=entry,
            candidates=candidates,
            blocks=blocks,
        )
