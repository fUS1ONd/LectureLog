from __future__ import annotations

import asyncio
import inspect
import logging
import math
from dataclasses import dataclass
from pathlib import Path

from lecturelog.domain.ports import ProgressCallback, UsageCallback

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Caption:
    start: float
    end: float
    text: str


async def emit_progress(callback: ProgressCallback | None, value: int) -> None:
    if callback is not None:
        result = callback(value)
        if inspect.isawaitable(result):
            await result


async def emit_usage(callback: UsageCallback | None, payload: dict) -> None:
    if callback is not None:
        result = callback(payload)
        if inspect.isawaitable(result):
            await result


async def probe_audio_seconds(audio_path: Path) -> float:
    """Best-effort ffprobe duration; zero means unknown."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v",
            "quiet",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await proc.communicate()
        if proc.returncode != 0:
            logger.warning("ffprobe завершился с кодом %s", proc.returncode)
            return 0.0
        value = float(out.decode().strip())
        return value if math.isfinite(value) and value > 0 else 0.0
    except (OSError, ValueError) as exc:
        logger.warning("Не удалось определить длительность аудио через ffprobe: %s", exc)
        return 0.0


def format_srt_timestamp(seconds: float) -> str:
    total_ms = max(0, int(round(seconds * 1000)))
    hours = total_ms // 3_600_000
    minutes = (total_ms % 3_600_000) // 60_000
    secs = (total_ms % 60_000) // 1000
    millis = total_ms % 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def normalize_captions(captions: list[Caption], *, duration: float | None = None) -> list[Caption]:
    limit = duration if duration and math.isfinite(duration) and duration > 0 else None
    normalized: list[Caption] = []
    cursor = 0.0
    for caption in sorted(captions, key=lambda item: item.start):
        timestamps_are_finite = all(math.isfinite(value) for value in (caption.start, caption.end))
        if not caption.text.strip() or not timestamps_are_finite:
            continue
        start = max(cursor, caption.start, 0.0)
        end = max(start, caption.end)
        if limit is not None:
            start = min(start, limit)
            end = min(end, limit)
        if end <= start:
            continue
        normalized.append(Caption(start, end, caption.text.strip()))
        cursor = end
    return normalized


def render_srt(captions: list[Caption]) -> str:
    blocks = [
        f"{index}\n{format_srt_timestamp(item.start)} --> "
        f"{format_srt_timestamp(item.end)}\n{item.text}"
        for index, item in enumerate(captions, 1)
    ]
    return "\n\n".join(blocks)


def write_srt_atomic(output_dir: Path, content: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / "transcript.srt"
    temporary = output_dir / ".transcript.srt.tmp"
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(target)
    return target
