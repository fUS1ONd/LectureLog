from __future__ import annotations

import asyncio
from pathlib import Path

from lecturelog.models import Section
from lecturelog.srt import parse_srt_time


def _ffmpeg_timestamp(value: str) -> str:
    total_ms = max(0, int(round(parse_srt_time(value) * 1000)))
    hours = total_ms // 3_600_000
    minutes = (total_ms % 3_600_000) // 60_000
    seconds = (total_ms % 60_000) // 1000
    millis = total_ms % 1000
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


async def cut_audio(audio_path: Path, sections: list[Section], output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    result: list[Path] = []

    for idx, section in enumerate(sections):
        target = output_dir / f"section_{idx + 1:02d}.mp3"
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-y",
            "-i",
            str(audio_path),
            "-ss",
            _ffmpeg_timestamp(section.start),
            "-to",
            _ffmpeg_timestamp(section.end),
            "-vn",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "192k",
            str(target),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(stderr.decode("utf-8", errors="ignore"))
        result.append(target)

    return result
