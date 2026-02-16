from lecturelog.srt import extract_plain_text, extract_srt_fragment, format_time, parse_srt_time


SRT_SAMPLE = """1
00:00:01,000 --> 00:00:03,000
Привет мир

2
00:00:04,000 --> 00:00:07,000
Это тест

3
00:00:08,500 --> 00:00:10,000
Финал
"""


def test_extract_plain_text_removes_indices_and_timestamps():
    assert extract_plain_text(SRT_SAMPLE) == "Привет мир Это тест Финал"


def test_parse_srt_time_supports_hh_mm_ss_and_mm_ss():
    assert parse_srt_time("00:01:30,500") == 90.5
    assert parse_srt_time("01:30,500") == 90.5


def test_format_time_normalizes_timestamp():
    assert format_time("00:01:30,500") == "00:01:30"
    assert format_time("00:01:30.500") == "00:01:30"


def test_extract_srt_fragment_by_time_window():
    fragment = extract_srt_fragment(SRT_SAMPLE, "00:00:03", "00:00:08")
    assert "Это тест" in fragment
    assert "Привет мир" in fragment
    assert "Финал" not in fragment
