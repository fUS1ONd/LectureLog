import asyncio
from pathlib import Path

from lecturelog.pipeline import slides


class _ProgressSpy:
    def __init__(self):
        self.calls = []

    def __call__(self, pct: int, message: str):
        self.calls.append((pct, message))


def test_convert_slides_pdf_uses_pdf_renderer(tmp_path, monkeypatch):
    pdf_path = tmp_path / "slides.pdf"
    pdf_path.write_bytes(b"%PDF-1.4")
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    expected = [out_dir / "slide-01.png", out_dir / "slide-02.png"]

    async def fake_render(path: Path, output_dir: Path):
        assert path == pdf_path
        assert output_dir == out_dir
        return expected

    monkeypatch.setattr(slides, "_render_pdf_to_png", fake_render)

    progress = _ProgressSpy()
    result = asyncio.run(slides.convert_slides(pdf_path, out_dir, progress))

    assert result == expected
    assert progress.calls


def test_convert_slides_pptx_converts_then_renders(tmp_path, monkeypatch):
    pptx_path = tmp_path / "slides.pptx"
    pptx_path.write_bytes(b"pptx")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    tmp_pdf = tmp_path / "converted.pdf"
    expected = [out_dir / "slide-01.png"]

    async def fake_convert(path: Path, work_dir: Path):
        assert path == pptx_path
        assert work_dir == out_dir
        return tmp_pdf

    async def fake_render(path: Path, output_dir: Path):
        assert path == tmp_pdf
        assert output_dir == out_dir
        return expected

    monkeypatch.setattr(slides, "_convert_pptx_to_pdf", fake_convert)
    monkeypatch.setattr(slides, "_render_pdf_to_png", fake_render)

    progress = _ProgressSpy()
    result = asyncio.run(slides.convert_slides(pptx_path, out_dir, progress))

    assert result == expected
    assert progress.calls
