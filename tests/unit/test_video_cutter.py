"""Юнит-тесты FfmpegVideoCutter: поведение нарезки без запуска ffmpeg.

A/V-синхронизация и длительности фрагментов проверяются интеграционно
(tests/integration/test_video_cutter_sync.py) на синтетическом видео.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

from lecturelog.domain.models import Section
from lecturelog.infrastructure.media.video_cutter import FfmpegVideoCutter


def _section(start: str = "00:00:15,000", end: str = "00:00:25,000") -> Section:
    return Section(title="s", start=start, end=end, content="")


async def _capture_cmd(source: Path, tmp_path: Path) -> list[str]:
    proc = AsyncMock()
    proc.communicate.return_value = (b"", b"")
    proc.returncode = 0
    with patch(
        "lecturelog.infrastructure.media.video_cutter.asyncio.create_subprocess_exec",
        return_value=proc,
    ) as spawn:
        await FfmpegVideoCutter().cut(source, [_section()], tmp_path)
    return list(spawn.call_args.args)


async def test_fragment_is_reencoded_h264_aac_mp4(tmp_path: Path) -> None:
    # Перекодирование вместо -c copy: см. докстринг FfmpegVideoCutter
    cmd = await _capture_cmd(Path("src.webm"), tmp_path)
    assert "copy" not in cmd
    assert "libx264" in cmd and "aac" in cmd
    assert cmd[-1].endswith(".mp4")  # контейнер всегда mp4, даже для webm-исходника


async def test_seek_before_input_and_duration_after(tmp_path: Path) -> None:
    # -ss до -i (быстрый входной seek, точность даёт перекодирование),
    # длительность выхода задаётся через -t, а не абсолютный -to
    cmd = await _capture_cmd(Path("src.mp4"), tmp_path)
    assert cmd.index("-ss") < cmd.index("-i") < cmd.index("-t")
    assert cmd[cmd.index("-ss") + 1] == "00:00:15.000"
    assert cmd[cmd.index("-t") + 1] == "10.000"
