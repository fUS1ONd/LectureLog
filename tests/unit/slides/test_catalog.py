import json
from pathlib import Path

import pytest

from lecturelog.domain.slides import SlideAsset
from lecturelog.infrastructure.slides.alignment.catalog import (
    catalog_batches,
    detect_boilerplate_lines,
    native_text_fallback,
    parse_catalog_response,
)


def _asset(number: int, text: str = "Алгоритмы и структуры данных") -> SlideAsset:
    return SlideAsset(
        number,
        Path(f"{number}.png"),
        "document",
        extracted_text=text,
        native_text_quality="good",
    )


def test_catalog_batches_are_bounded_and_ordered() -> None:
    batches = catalog_batches([_asset(number) for number in range(1, 15)])
    assert [len(batch) for batch in batches] == [2, 2, 2, 2, 2, 2, 2]
    assert [item.slide_num for batch in batches for item in batch] == list(range(1, 15))


def test_catalog_rejects_shuffled_or_extra_response() -> None:
    raw = json.dumps(
        {
            "slides": [
                {"slide_num": 2, "role": "content", "visible_text": "two"},
                {"slide_num": 1, "role": "content", "visible_text": "one"},
            ]
        }
    )
    with pytest.raises(ValueError):
        parse_catalog_response(raw, [1, 2])


def test_native_text_fallback_is_unresolved_without_text() -> None:
    unresolved = native_text_fallback(_asset(1, ""))
    assert unresolved.status == "unresolved"
    assert native_text_fallback(_asset(2)).status == "native_text_fallback"


def test_detect_boilerplate_lines_finds_repeated_header() -> None:
    """Колонтитул курса повторяется на каждой странице и не должен считаться содержанием."""
    assets = [
        _asset(1, "Разработка программного обеспечения\nЛекция 1: О программной инженерии"),
        _asset(2, "Разработка программного обеспечения\nОрганизационное"),
        _asset(3, "Разработка программного обеспечения\nЖизненный цикл"),
    ]
    boilerplate = detect_boilerplate_lines(assets)
    assert "Разработка программного обеспечения" in boilerplate
    assert "Организационное" not in boilerplate


def test_native_fallback_drops_boilerplate_from_title_and_concepts() -> None:
    """Колонтитул не должен становиться заголовком слайда и доказательным концептом."""
    asset = _asset(2, "Разработка программного обеспечения\nОрганизационное\nECTS и баллы")
    result = native_text_fallback(
        asset, boilerplate=frozenset({"Разработка программного обеспечения"})
    )
    assert result.entry is not None
    assert result.entry.title == "Организационное"
    assert "Разработка программного обеспечения" not in result.entry.source_concepts
    assert "ECTS и баллы" in result.entry.source_concepts


def test_native_fallback_drops_boilerplate_from_visible_text() -> None:
    """grounding строит claim в том числе из visible_text — колонтитул нужно убрать и оттуда."""
    asset = _asset(2, "Разработка программного обеспечения\nОрганизационное\nECTS и баллы")
    result = native_text_fallback(
        asset, boilerplate=frozenset({"Разработка программного обеспечения"})
    )
    assert result.entry is not None
    assert "Разработка программного обеспечения" not in result.entry.visible_text
    assert "ECTS и баллы" in result.entry.visible_text
