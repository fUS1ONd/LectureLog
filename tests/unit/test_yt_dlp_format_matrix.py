import json
import subprocess

import pytest

from lecturelog.infrastructure.media.video_ingestor import VideoIngestor


def _format(
    format_id,
    width,
    height,
    *,
    protocol="https",
    tbr=1000,
):
    return {
        "format_id": format_id,
        "url": f"https://media.example/{format_id}.mp4",
        "ext": "mp4",
        "width": width,
        "height": height,
        "tbr": tbr,
        "protocol": protocol,
        "vcodec": "avc1.64001f",
        "acodec": "mp4a.40.2",
    }


def _select(tmp_path, formats, sort):
    info = {
        "id": "synthetic",
        "title": "Synthetic format matrix",
        "extractor": "generic",
        "extractor_key": "Generic",
        "webpage_url": "https://media.example/watch/synthetic",
        "formats": formats,
    }
    info_path = tmp_path / "info.json"
    info_path.write_text(json.dumps(info), encoding="utf-8")
    result = subprocess.run(
        [
            VideoIngestor._yt_dlp_bin(),
            "--load-info-json",
            str(info_path),
            "--simulate",
            "-f",
            "bv*+ba/b",
            "-S",
            sort,
            "--print",
            "%(format_id)s",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def test_target_720_prefers_progressive_https_at_same_landscape_resolution(tmp_path):
    formats = [
        _format("progressive-360", 640, 360, tbr=500),
        _format("hls-720", 1280, 720, protocol="m3u8_native", tbr=1800),
        _format("progressive-720", 1280, 720, tbr=1500),
        _format("hls-1080", 1920, 1080, protocol="m3u8_native", tbr=3000),
    ]
    assert _select(tmp_path, formats, "res:720,proto:https") == "progressive-720"


def test_target_720_is_orientation_independent_for_portrait_video(tmp_path):
    formats = [
        _format("portrait-568", 320, 568, tbr=500),
        _format("portrait-720", 720, 1280, tbr=1500),
        _format("portrait-1080", 1080, 1920, tbr=3000),
    ]
    assert _select(tmp_path, formats, "res:720,proto:https") == "portrait-720"


def test_target_uses_smallest_format_above_when_nothing_is_below(tmp_path):
    formats = [
        _format("above-1080", 1920, 1080, tbr=3000),
        _format("above-1440", 2560, 1440, tbr=5000),
    ]
    assert _select(tmp_path, formats, "res:720,proto:https") == "above-1080"


def test_best_keeps_quality_ahead_of_progressive_protocol(tmp_path):
    formats = [
        _format("progressive-720", 1280, 720, tbr=1500),
        _format("hls-1080", 1920, 1080, protocol="m3u8_native", tbr=3000),
    ]
    assert _select(tmp_path, formats, "res,proto:https") == "hls-1080"


@pytest.mark.parametrize("target", ["720", "best"])
def test_ingestor_sort_contract_matches_matrix(target):
    expected = "res,proto:https" if target == "best" else "res:720,proto:https"
    assert VideoIngestor(target_resolution=target)._format_sort() == expected
