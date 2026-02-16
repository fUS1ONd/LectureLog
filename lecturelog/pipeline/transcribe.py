from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from typing import Any, Callable

import httpx

GROQ_TRANSCRIBE_URL = "https://api.groq.com/openai/v1/audio/transcriptions"


def _format_srt_timestamp(seconds: float) -> str:
    total_ms = max(0, int(round(seconds * 1000)))
    hours = total_ms // 3_600_000
    minutes = (total_ms % 3_600_000) // 60_000
    secs = (total_ms % 60_000) // 1000
    millis = total_ms % 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _build_srt_from_words(words: list[dict[str, Any]], words_per_caption: int = 7) -> str:
    if not words:
        return ""

    lines: list[str] = []
    sorted_words = sorted(words, key=lambda item: float(item["start"]))
    for idx in range(0, len(sorted_words), words_per_caption):
        group = sorted_words[idx : idx + words_per_caption]
        if not group:
            continue

        block_index = idx // words_per_caption + 1
        start_ts = _format_srt_timestamp(float(group[0]["start"]))
        end_ts = _format_srt_timestamp(float(group[-1]["end"]))
        text = " ".join(str(item["word"]).strip() for item in group if str(item["word"]).strip())

        lines.append(str(block_index))
        lines.append(f"{start_ts} --> {end_ts}")
        lines.append(text)
        lines.append("")

    return "\n".join(lines).strip()


async def _emit_progress(on_progress: Callable[[int], Any], value: int) -> None:
    maybe_awaitable = on_progress(value)
    if inspect.isawaitable(maybe_awaitable):
        await maybe_awaitable


async def _run_ffmpeg_segment(audio_path: Path, output_dir: Path) -> None:
    output_pattern = output_dir / "chunk_%03d.mp3"
    process = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",
        "-i",
        str(audio_path),
        "-f",
        "segment",
        "-segment_time",
        "1200",
        "-c:a",
        "libmp3lame",
        "-b:a",
        "128k",
        str(output_pattern),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError(f"ffmpeg завершился с ошибкой: {stderr.decode('utf-8', errors='ignore')}")


async def _transcribe_chunk(
    client: httpx.AsyncClient,
    chunk_path: Path,
    groq_api_key: str,
    offset_seconds: float,
    semaphore: asyncio.Semaphore,
) -> list[dict[str, Any]]:
    async with semaphore:
        headers = {"Authorization": f"Bearer {groq_api_key}"}
        data = [
            ("model", "whisper-large-v3"),
            ("response_format", "verbose_json"),
            ("timestamp_granularities[]", "word"),
        ]
        files = {
            "file": (
                chunk_path.name,
                chunk_path.read_bytes(),
                "audio/mpeg",
            )
        }
        response = await client.post(
            GROQ_TRANSCRIBE_URL,
            headers=headers,
            data=data,
            files=files,
        )
        response.raise_for_status()
        payload = response.json()
        words: list[dict[str, Any]] = payload.get("words") or []
        result: list[dict[str, Any]] = []
        for item in words:
            word = str(item.get("word", "")).strip()
            start = item.get("start")
            end = item.get("end")
            if not word or start is None or end is None:
                continue
            result.append(
                {
                    "word": word,
                    "start": float(start) + offset_seconds,
                    "end": float(end) + offset_seconds,
                }
            )
        return result


async def transcribe(
    audio_path: Path,
    output_dir: Path,
    groq_api_key: str,
    on_progress: Callable[[int], Any],
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    await _emit_progress(on_progress, 5)

    await _run_ffmpeg_segment(audio_path, output_dir)
    chunk_paths = sorted(output_dir.glob("chunk_*.mp3"))
    if not chunk_paths:
        raise RuntimeError("ffmpeg не создал сегменты аудио")

    await _emit_progress(on_progress, 20)

    semaphore = asyncio.Semaphore(6)
    words: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=120.0) as client:
        tasks = [
            asyncio.create_task(
                _transcribe_chunk(
                    client=client,
                    chunk_path=chunk_path,
                    groq_api_key=groq_api_key,
                    offset_seconds=index * 1200.0,
                    semaphore=semaphore,
                )
            )
            for index, chunk_path in enumerate(chunk_paths)
        ]

        total = len(tasks)
        done = 0
        for task in asyncio.as_completed(tasks):
            chunk_words = await task
            words.extend(chunk_words)
            done += 1
            await _emit_progress(on_progress, 20 + int((done / total) * 70))

    srt_content = _build_srt_from_words(words)
    srt_path = output_dir / "transcript.srt"
    srt_path.write_text(srt_content, encoding="utf-8")

    await _emit_progress(on_progress, 100)
    return srt_path

