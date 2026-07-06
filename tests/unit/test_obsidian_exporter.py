import pytest

from lecturelog.domain.models import Section, Topic
from lecturelog.domain.ports import SlideImage
from lecturelog.infrastructure.export.obsidian_exporter import ObsidianExporter, _slugify


def test_slugify_cyrillic_and_spaces():
    assert _slugify("Введение в тему") == "введение-в-тему"


def test_slugify_strips_punctuation():
    out = _slugify("Раздел #1: основы!")
    assert " " not in out and "#" not in out and ":" not in out


def test_slugify_empty_falls_back():
    assert _slugify("!!!") == "section"


@pytest.mark.asyncio
async def test_export_lays_out_output_dir_and_returns_targets(tmp_path):
    # подготовим фейковые фрагменты и слайды
    frag = tmp_path / "f1.mp3"
    frag.write_bytes(b"audio")
    slide = tmp_path / "s1.png"
    slide.write_bytes(b"png")
    sec = Section(title="Введение", start="0:00", end="5:00", content="текст", slide_indices=[1])
    topic = Topic(title="Тема", start="0:00", end="5:00", sections=[sec], slide_indices=[1])

    output_dir = tmp_path / "export"
    exporter = ObsidianExporter()
    result = await exporter.export(
        topics=[topic],
        media_fragments=[frag],
        slide_images=[SlideImage(path=slide)],
        output_dir=output_dir,
        media_kind="audio",
    )

    # Exporter раскладывает output/ на диск и возвращает ExportResult (без zip).
    output_root = output_dir / "output"
    assert result.output_root == output_root
    assert (output_root / "конспект.md").exists()
    # media_targets/slide_targets — фактические пути на диске.
    assert len(result.media_targets) == 1
    assert result.media_targets[0].exists()
    assert result.media_targets[0].parent.name == "audio"
    assert len(result.slide_targets) == 1
    assert result.slide_targets[0].exists()
    assert result.slide_targets[0].name == "slide-01.png"
    # result.zip больше НЕ создаётся.
    assert not (output_dir / "result.zip").exists()
