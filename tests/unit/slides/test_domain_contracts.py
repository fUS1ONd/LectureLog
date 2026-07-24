from pathlib import Path

import pytest

from lecturelog.domain.slides import SlideAsset, SlideCatalogResult


def test_slide_asset_origin_invariants() -> None:
    document = SlideAsset(
        1,
        Path("one.png"),
        "document",
        extracted_text="",
        native_text_quality="none",
    )
    video = SlideAsset(1, Path("frame.png"), "video", timestamp=1.5)
    assert document.timestamp is None
    assert video.timestamp == 1.5
    with pytest.raises(ValueError):
        SlideAsset(1, Path("bad.png"), "document")
    with pytest.raises(ValueError):
        SlideAsset(1, Path("bad.png"), "video")


def test_catalog_result_discriminated_contract() -> None:
    assert SlideCatalogResult(1, "unresolved", None).entry is None
    with pytest.raises(ValueError):
        SlideCatalogResult(1, "verified", None)
