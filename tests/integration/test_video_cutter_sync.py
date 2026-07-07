"""A/V-синхронизация нарезанных фрагментов.

Регрессия: -c copy при разрезе в середине GOP кладёт во фрагмент видео-преролл
с discard-флагами от предыдущего keyframe (до GOP-длины), а аудио-преролл лишь
~1 c — корректность зависит от поддержки mp4 edit list плеером; кто её не
уважает, получает рассинхрон. Фрагмент обязан начинаться с keyframe около нуля
и без опоры на edit list."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from lecturelog.domain.models import Section
from lecturelog.infrastructure.media.video_cutter import FfmpegVideoCutter

# GOP 10 секунд — как у типичного YouTube-исходника
_GOP_FRAMES = 300


@pytest.fixture(scope="module")
def synthetic_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """30 c: testsrc2 + синус, H.264 с редкими keyframes (0, 10, 20 c)."""
    path = tmp_path_factory.mktemp("avsync") / "src.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", "testsrc2=duration=30:size=320x180:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=30",
            "-c:v", "libx264", "-preset", "ultrafast",
            "-g", str(_GOP_FRAMES), "-keyint_min", str(_GOP_FRAMES), "-sc_threshold", "0",
            "-c:a", "aac", "-shortest", str(path),
        ],
        check=True,
    )  # fmt: skip
    return path


def _probe_packets(path: Path, stream: str, count: int = 8) -> list[dict]:
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", stream,
            "-show_entries", "packet=pts_time,flags",
            "-of", "json", str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout  # fmt: skip
    return json.loads(out)["packets"][:count]


async def test_mid_gop_cut_has_no_discard_preroll_and_av_streams_aligned(
    synthetic_video: Path, tmp_path: Path
) -> None:
    # Разрез на 15-й секунде — ровно середина GOP (keyframes на 0/10/20)
    section = Section(title="s", start="00:00:15,000", end="00:00:25,000", content="")
    (frag,) = await FfmpegVideoCutter().cut(synthetic_video, [section], tmp_path)

    video = _probe_packets(frag, "v")
    audio = _probe_packets(frag, "a")

    # Ни одного discard-пакета и отрицательного pts: плеер без поддержки
    # edit list не должен видеть контент до запрошенной границы
    assert all("D" not in p["flags"] for p in video), video
    assert float(video[0]["pts_time"]) >= -0.001
    assert float(audio[0]["pts_time"]) >= -0.05

    # Видео начинается с keyframe у нуля — старт мгновенный и синхронный
    assert "K" in video[0]["flags"]
    assert float(video[0]["pts_time"]) <= 0.2

    # Потоки стартуют вместе (допуск — один AAC-фрейм ~23 мс)
    assert abs(float(video[0]["pts_time"]) - float(audio[0]["pts_time"])) <= 0.05


async def test_fragment_duration_matches_requested_range(
    synthetic_video: Path, tmp_path: Path
) -> None:
    section = Section(title="s", start="00:00:15,000", end="00:00:25,000", content="")
    (frag,) = await FfmpegVideoCutter().cut(synthetic_video, [section], tmp_path)
    out = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration", "-of", "json", str(frag),
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout  # fmt: skip
    duration = float(json.loads(out)["format"]["duration"])
    assert 9.5 <= duration <= 10.5
