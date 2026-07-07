"""Тесты placement: сегментация markdown на абзацы и расстановка маркеров слайдов."""

from pathlib import Path

from lecturelog.domain.models import Section, Topic
from lecturelog.domain.ports import SlideImage
from lecturelog.infrastructure.frames.placement import place_slides_in_sections, split_paragraphs

# --- split_paragraphs ---


def test_split_by_blank_lines():
    md = "Первый абзац.\n\nВторой абзац.\n\nТретий."
    assert split_paragraphs(md) == ["Первый абзац.", "Второй абзац.", "Третий."]


def test_code_fence_with_blank_lines_not_split():
    md = "Текст.\n\n```python\nx = 1\n\ny = 2\n```\n\nПосле."
    parts = split_paragraphs(md)
    assert parts == ["Текст.", "```python\nx = 1\n\ny = 2\n```", "После."]


def test_multiple_blank_lines_collapse():
    md = "Один.\n\n\n\nДва."
    assert split_paragraphs(md) == ["Один.", "Два."]


def test_empty_content():
    assert split_paragraphs("") == []
    assert split_paragraphs("\n\n") == []


# --- place_slides_in_sections ---


def _img(ts):
    return SlideImage(path=Path(f"f{ts}.jpg"), timestamp=float(ts))


def _topic(sections):
    return Topic(title="Тема", start=sections[0].start, end=sections[-1].end, sections=sections)


def test_marker_lands_after_paragraph_by_timestamp():
    # Секция 03:00–06:00 (180 c), 3 равных абзаца по 60 с; слайд ts=250 -> 2-й абзац
    sec = Section(
        title="A",
        start="00:03:00",
        end="00:06:00",
        content="Абзац раз.\n\nАбзац два.\n\nАбзац три.",
        slide_indices=[1],
    )
    topics = [_topic([sec])]
    place_slides_in_sections([_img(250)], topics)
    assert sec.content == "Абзац раз.\n\nАбзац два.\n\n<!-- slide:1 -->\n\nАбзац три."


def test_weighting_by_paragraph_length():
    # Первый абзац в 9 раз длиннее второго: занимает 90% времени секции.
    # Секция 0–100 c, слайд ts=50 попадает в первый абзац (при равномерной
    # пропорции попал бы во второй).
    long_par = "х" * 900
    sec = Section(
        title="A",
        start="00:00:00",
        end="00:01:40",
        content=f"{long_par}\n\nкороткий",
        slide_indices=[1],
    )
    topics = [_topic([sec])]
    place_slides_in_sections([_img(50)], topics)
    assert sec.content == f"{long_par}\n\n<!-- slide:1 -->\n\nкороткий"


def test_section_without_slides_untouched():
    sec = Section(title="A", start="00:00:00", end="00:01:00", content="Текст.\n\nЕщё.")
    topics = [_topic([sec])]
    place_slides_in_sections([_img(10)], topics)
    assert sec.content == "Текст.\n\nЕщё."


def test_ts_outside_section_goes_after_last_paragraph():
    # Кадр прижат монотонизацией: ts=10 при секции 05:00–06:00 -> в конец
    sec = Section(
        title="A",
        start="00:05:00",
        end="00:06:00",
        content="Один.\n\nДва.",
        slide_indices=[1],
    )
    topics = [_topic([sec])]
    place_slides_in_sections([_img(9999)], topics)
    assert sec.content == "Один.\n\nДва.\n\n<!-- slide:1 -->"


def test_two_slides_same_paragraph_keep_ts_order():
    sec = Section(
        title="A",
        start="00:00:00",
        end="00:03:00",
        content="Один.\n\nДва.\n\nТри.",
        slide_indices=[1, 2],
    )
    topics = [_topic([sec])]
    place_slides_in_sections([_img(70), _img(80)], topics)
    assert sec.content == "Один.\n\nДва.\n\n<!-- slide:1 -->\n\n<!-- slide:2 -->\n\nТри."


def test_empty_content_gets_markers_only():
    sec = Section(title="A", start="00:00:00", end="00:01:00", content="", slide_indices=[1])
    topics = [_topic([sec])]
    place_slides_in_sections([_img(30)], topics)
    assert sec.content == "<!-- slide:1 -->"
