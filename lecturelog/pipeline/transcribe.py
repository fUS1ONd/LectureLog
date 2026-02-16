from __future__ import annotations

import asyncio
import math
from pathlib import Path
from typing import Callable

import httpx


def _srt_ts(seconds: float) -> str:
    ms = int((seconds - int(seconds)) * 1000)
    total = int(seconds)
    hh = total // 3600
    mm = (total % 3600) // 60
    ss = total % 60
    return f"{hh:02d}:{mm:02d}:{ss:02d},{ms:03d}"


async def _run_ffmpeg_segment(audio_path: Path, output_dir: Path):
    output_pattern = output_dir / "chunk_%03d.mp3"
    proc = await asyncio.create_subprocess_exec(
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
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg segment failed: {stderr.decode('utf-8', errors='ignore')}")


async def transcribe(
    audio_path: Path,
    output_dir: Path,
    groq_api_key: str,
    on_progress: Callable[[int, str], None],
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    chunks_dir = output_dir / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)

    on_progress(5, "Подготовка аудио")
    await _run_ffmpeg_segment(audio_path, chunks_dir)

    chunk_paths = sorted(chunks_dir.glob("chunk_*.mp3"))
    if not chunk_paths:
        fallback = chunks_dir / "chunk_000.mp3"
        fallback.write_bytes(audio_path.read_bytes())
        chunk_paths = [fallback]

    semaphore = asyncio.Semaphore(6)

    async with httpx.AsyncClient(timeout=120) as client:
        async def transcribe_chunk(chunk_path: Path, chunk_index: int):
            async with semaphore:
                files = {"file": (chunk_path.name, chunk_path.read_bytes(), "audio/mpeg")}
                data = {
                    "model": "whisper-large-v3",
                    "response_format": "verbose_json",
                    "timestamp_granularities[]": "word",
                }
                headers = {"Authorization": f"Bearer {groq_api_key}"}
                resp = await client.post(
                    "https://api.groq.com/openai/v1/audio/transcriptions",
                    data=data,
                    files=files,
                    headers=headers,
                )
                resp.raise_for_status()
                payload = resp.json()
                words = payload.get("words", [])
                offset = chunk_index * 1200.0
                normalized: list[tuple[float, float, str]] = []
                for word in words:
                    start = float(word.get("start", 0.0)) + offset
                    end = float(word.get("end", start + 0.2)) + offset
                    token = str(word.get("word", "")).strip()
                    if token:
                        normalized.append((start, end, token))
                return normalized

        tasks = [transcribe_chunk(path, i) for i, path in enumerate(chunk_paths)]
        words_per_chunk = await asyncio.gather(*tasks)

    all_words = [word for chunk_words in words_per_chunk for word in chunk_words]
    if not all_words:
        raise RuntimeError("Groq Whisper вернул пустой результат")

    group_size = 7
    entries: list[str] = []
    for idx in range(math.ceil(len(all_words) / group_size)):
        chunk = all_words[idx * group_size : (idx + 1) * group_size]
        start = _srt_ts(chunk[0][0])
        end = _srt_ts(chunk[-1][1])
        text = " ".join(token for _, _, token in chunk)
        entries.append(f"{idx + 1}\n{start} --> {end}\n{text}\n")

    srt_path = output_dir / "transcript.srt"
    srt_path.write_text("\n".join(entries), encoding="utf-8")
    on_progress(100, "Транскрибация завершена")
    return srt_path
