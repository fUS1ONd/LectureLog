from pathlib import Path

from lecturelog.domain.models import Section, Topic
from lecturelog.domain.ports import SlideImage
from lecturelog.infrastructure.frames.binding import bind_frames_to_sections


def _topic(sections):
    return Topic(title="Тема", start=sections[0].start, end=sections[-1].end, sections=sections)


def _sections():
    return [
        Section(title="A", start="00:00:00", end="00:05:00", content=""),
        Section(title="B", start="00:05:00", end="00:10:00", content=""),
        Section(title="C", start="00:10:00", end="00:15:00", content=""),
    ]


def _img(ts):
    return SlideImage(path=Path(f"f{ts}.jpg"), timestamp=float(ts))


def test_frames_land_in_their_sections():
    topics = [_topic(_sections())]
    bind_frames_to_sections([_img(30), _img(400), _img(700)], topics)
    secs = topics[0].sections
    assert secs[0].slide_indices == [1]
    assert secs[1].slide_indices == [2]
    assert secs[2].slide_indices == [3]
    assert topics[0].slide_indices == [1, 2, 3]


def test_ts_beyond_last_section_clamps_to_last():
    topics = [_topic(_sections())]
    bind_frames_to_sections([_img(9999)], topics)
    assert topics[0].sections[2].slide_indices == [1]


def test_monotonic_no_backward_jumps():
    # Кадры отсортированы по ts → привязка не скачет назад по секциям
    topics = [_topic(_sections())]
    bind_frames_to_sections([_img(400), _img(410), _img(420)], topics)
    assert topics[0].sections[1].slide_indices == [1, 2, 3]


def test_document_slides_are_rejected():
    import pytest

    topics = [_topic(_sections())]
    with pytest.raises(ValueError):
        bind_frames_to_sections([SlideImage(path=Path("s.png"))], topics)
