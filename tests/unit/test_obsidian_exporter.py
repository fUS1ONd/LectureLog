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
    assert result.slide_targets[1].exists()
    assert result.slide_targets[1].name == "slide-01.png"
    # result.zip больше НЕ создаётся.
    assert not (output_dir / "result.zip").exists()


async def test_export_replaces_slide_markers_inline(tmp_path):
    # Маркер <!-- slide:N --> в content заменяется картинкой на месте,
    # блока слайдов перед текстом при этом нет.
    frag = tmp_path / "f1.mp3"
    frag.write_bytes(b"audio")
    slide = tmp_path / "s1.png"
    slide.write_bytes(b"png")
    sec = Section(
        title="Введение",
        start="0:00",
        end="5:00",
        content="Абзац раз.\n\n<!-- slide:1 -->\n\nАбзац два.",
        slide_indices=[1],
    )
    topic = Topic(title="Тема", start="0:00", end="5:00", sections=[sec], slide_indices=[1])

    exporter = ObsidianExporter()
    result = await exporter.export(
        topics=[topic],
        media_fragments=[frag],
        slide_images=[SlideImage(path=slide, timestamp=10.0, caption="Заставка")],
        output_dir=tmp_path / "export",
        media_kind="audio",
    )
    md = (result.output_root / "конспект.md").read_text(encoding="utf-8")
    assert "<!-- slide:1 -->" not in md
    assert "Абзац раз.\n\n![Заставка](slides/slide-01.png)\n\nАбзац два." in md
    # Ровно одно вхождение картинки — нет дубля блоком перед текстом.
    assert md.count("slides/slide-01.png") == 1


async def test_export_slides_without_marker_fall_back_to_block(tmp_path):
    # Кадр привязан к секции, но маркера в тексте нет (старое поведение,
    # документные слайды) -> блок перед контентом, как раньше.
    frag = tmp_path / "f1.mp3"
    frag.write_bytes(b"audio")
    slide = tmp_path / "s1.png"
    slide.write_bytes(b"png")
    sec = Section(title="В", start="0:00", end="5:00", content="Текст.", slide_indices=[1])
    topic = Topic(title="Т", start="0:00", end="5:00", sections=[sec], slide_indices=[1])

    exporter = ObsidianExporter()
    result = await exporter.export(
        topics=[topic],
        media_fragments=[frag],
        slide_images=[SlideImage(path=slide)],
        output_dir=tmp_path / "export",
        media_kind="audio",
    )
    md = (result.output_root / "конспект.md").read_text(encoding="utf-8")
    assert "![Слайд 1](slides/slide-01.png)\n\nТекст." in md
