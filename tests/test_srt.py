from lecturelog.srt import extract_plain_text, extract_srt_fragment, format_time, parse_srt_time


SRT_SAMPLE = """1
00:00:00,000 --> 00:00:02,000
Всем привет

2
00:00:02,500 --> 00:00:05,000
Сегодня говорим про алгоритмы

3
00:00:05,500 --> 00:00:07,000
И структуры данных
"""


def test_extract_plain_text_removes_indexes_and_timestamps() -> None:
    text = extract_plain_text(SRT_SAMPLE)
    assert text == "Всем привет Сегодня говорим про алгоритмы И структуры данных"


def test_parse_srt_time_and_format_time() -> None:
    assert parse_srt_time("00:01:02,500") == 62.5
    assert parse_srt_time("01:02,250") == 62.25
    assert format_time("00:10:12,777") == "00:10:12"
    assert format_time("00:10:12.777") == "00:10:12"


def test_extract_srt_fragment_by_time_range() -> None:
    fragment = extract_srt_fragment(SRT_SAMPLE, "00:00:02", "00:00:06")
    assert "Сегодня говорим про алгоритмы" in fragment
    assert "И структуры данных" in fragment
    assert "Всем привет" in fragment

