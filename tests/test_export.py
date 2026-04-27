import asyncio
from pathlib import Path
from zipfile import ZipFile

from lecturelog.models import Section
from lecturelog.pipeline.export import export_result


def test_export_result_creates_zip_with_markdown_and_assets(tmp_path):
    audio_src = tmp_path / "a.mp3"
    audio_src.write_bytes(b"audio")

    slide_src = tmp_path / "s.png"
    slide_src.write_bytes(b"img")

    sections = [
        Section(
            title="Введение в системы",
            start="00:00:00",
            end="00:01:00",
            content="Текст раздела",
            slide_indices=[1],
        )
    ]

    zip_path = asyncio.run(
        export_result(
            sections=sections,
            audio_fragments=[audio_src],
            slide_images=[slide_src],
            output_dir=tmp_path / "result",
        )
    )

    assert zip_path.exists()

    with ZipFile(zip_path, "r") as zf:
        names = set(zf.namelist())
        assert "output/конспект.md" in names
        assert "output/audio/01-введение-в-системы.mp3" in names
        assert "output/slides/slide-01.png" in names

        md = zf.read("output/конспект.md").decode("utf-8")
        assert "## Введение в системы" in md
        assert "[00:00:00 - 00:01:00]" in md
        assert "audio/01-введение-в-системы.mp3" in md
        assert "slides/slide-01.png" in md
