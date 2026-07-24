from lecturelog.infrastructure.srt import extract_srt_fragment, parse_srt_blocks


def test_parse_srt_blocks_handles_crlf_multiline_and_dot_milliseconds() -> None:
    blocks = parse_srt_blocks(
        "7\r\n00:00:01.250 --> 00:00:02,750\r\nfirst\r\nsecond\r\n\r\n"
        "8\r\n00:00:03,000 --> 00:00:04,000\r\nthird\r\n"
    )
    assert [(block.block_id, block.start_s, block.text) for block in blocks] == [
        (1, 1.25, "first second"),
        (2, 3.0, "third"),
    ]
    assert "first second" in extract_srt_fragment(
        "7\n00:00:01.250 --> 00:00:02,750\nfirst\nsecond\n", "0:01", "0:02"
    )

