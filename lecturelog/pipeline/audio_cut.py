from __future__ import annotations

import asyncio
from pathlib import Path

from lecturelog.models import Section


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
            section.start,
            "-to",
            section.end,
            "-c",
            "copy",
            str(target),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(stderr.decode("utf-8", errors="ignore"))
        result.append(target)

    return result
