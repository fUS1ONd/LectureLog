from pathlib import Path

import pytest

from lecturelog.infrastructure.media.url_utils import (
    VideoUrlKind,
    classify_video_url,
    is_url,
)


def test_http_url_is_url():
    assert is_url("http://example.com/v") is True


def test_https_url_is_url():
    assert is_url("https://youtu.be/abc") is True


def test_plain_path_is_not_url():
    assert is_url("/tmp/lecture.mp4") is False
    assert is_url("lecture.mp4") is False


def test_scheme_without_netloc_is_not_url():
    # "youtube.com/watch?v=..." без схемы — не URL (паритет с PoC)
    assert is_url("youtube.com/watch?v=x") is False


def test_path_object_is_not_url():
    assert is_url(Path("/tmp/v.mp4")) is False


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://x.com/i/status/1", VideoUrlKind.X),
        ("https://WWW.X.COM./u/status/1", VideoUrlKind.X),
        ("https://mobile.twitter.com/u/status/1", VideoUrlKind.X),
        ("https://m.twitter.com/u/status/1", VideoUrlKind.X),
        ("https://youtube.com/watch?v=x", VideoUrlKind.YOUTUBE),
        ("https://music.youtube.com/watch?v=x", VideoUrlKind.YOUTUBE),
        ("https://youtu.be/x", VideoUrlKind.YOUTUBE),
        ("https://x.com.evil.example/video", VideoUrlKind.GENERIC),
        ("https://notyoutube.com/video", VideoUrlKind.GENERIC),
        ("https://cdn.example/video.mp4", VideoUrlKind.GENERIC),
    ],
)
def test_classify_video_url_uses_exact_normalized_hostname(url, expected):
    assert classify_video_url(url) is expected
