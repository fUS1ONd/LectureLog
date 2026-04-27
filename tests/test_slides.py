import asyncio
from pathlib import Path

import lecturelog.pipeline.slides as slides_module
from lecturelog.pipeline.slides import convert_slides


def test_convert_slides_pdf_calls_pdf_converter(tmp_path, monkeypatch) -> None:
    source = tmp_path / "deck.pdf"
    source.write_bytes(b"pdf")
    output_dir = tmp_path / "out"

    expected = [output_dir / "slide-01.png", output_dir / "slide-02.png"]

    async def fake_convert_pdf(path: Path, out_dir: Path) -> list[Path]:
        assert path == source
        assert out_dir == output_dir
        return expected

    monkeypatch.setattr(slides_module, "_convert_pdf_to_png", fake_convert_pdf)

    result = asyncio.run(convert_slides(source, output_dir, on_progress=lambda _: None))

    assert result == expected


def test_convert_slides_pptx_calls_pptx_converter(tmp_path, monkeypatch) -> None:
    source = tmp_path / "deck.pptx"
    source.write_bytes(b"pptx")
    output_dir = tmp_path / "out"

    expected = [output_dir / "slide-01.png"]

    async def fake_convert_pptx(path: Path, out_dir: Path) -> list[Path]:
        assert path == source
        assert out_dir == output_dir
        return expected

    monkeypatch.setattr(slides_module, "_convert_pptx_to_png", fake_convert_pptx)

    result = asyncio.run(convert_slides(source, output_dir, on_progress=lambda _: None))

    assert result == expected


def test_convert_slides_unsupported_extension_raises(tmp_path) -> None:
    source = tmp_path / "deck.txt"
    source.write_text("x", encoding="utf-8")

    async def scenario() -> None:
        await convert_slides(source, tmp_path / "out", on_progress=lambda _: None)

    try:
        asyncio.run(scenario())
    except ValueError as error:
        assert "Неподдерживаемый формат" in str(error)
    else:
        raise AssertionError("Ожидался ValueError для неподдерживаемого формата")

