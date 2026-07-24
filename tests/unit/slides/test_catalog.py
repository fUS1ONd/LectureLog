import json
from pathlib import Path

import pytest

from lecturelog.domain.slides import SlideAsset
from lecturelog.infrastructure.slides.alignment.catalog import (
    catalog_batches,
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
    assert [len(batch) for batch in batches] == [6, 6, 2]
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
