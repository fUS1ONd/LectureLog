from pathlib import Path

import pytest

from lecturelog.infrastructure.slides.document_provider import DocumentSlideProvider


def _make_pdf(path: Path, pages: int):
    import pymupdf

    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"Slide {i + 1}")
    doc.save(str(path))
    doc.close()


@pytest.mark.asyncio
async def test_pdf_renders_one_png_per_page(tmp_path):
    pdf = tmp_path / "deck.pdf"
    _make_pdf(pdf, pages=3)
    provider = DocumentSlideProvider(slides_path=pdf)
    out = await provider.get_slides(output_dir=tmp_path / "slides")
    assert len(out) == 3
    assert all(item.path.exists() and item.path.suffix == ".png" for item in out)
    # имена по порядку slide-01.png, slide-02.png...
    assert out[0].path.name == "slide-01.png"


@pytest.mark.asyncio
async def test_get_slides_returns_slide_images_without_timestamp(tmp_path):
    pdf = tmp_path / "deck.pdf"
    _make_pdf(pdf, pages=2)
    provider = DocumentSlideProvider(slides_path=pdf)
    items = await provider.get_slides(output_dir=tmp_path / "out")
    assert all(item.timestamp is None and item.caption is None for item in items)
    assert [item.path.name for item in items] == ["slide-01.png", "slide-02.png"]
