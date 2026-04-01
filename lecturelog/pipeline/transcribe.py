from __future__ import annotations

import asyncio
import inspect
import time
from pathlib import Path
from typing import Any, Callable

import httpx

GROQ_TRANSCRIBE_URL = "https://api.groq.com/openai/v1/audio/transcriptions"


class GroqKeyPool:
    """Round-robin пул Groq API ключей с блокировкой при rate limit."""

    def __init__(self, keys: list[str]):
        if not keys:
            raise ValueError("Нужен хотя бы один Groq API ключ")
        self._keys = keys
        self._blocked_until: list[float] = [0.0] * len(keys)
        self._next_idx = 0
        self._lock = asyncio.Lock()

    async def acquire(self) -> str:
        """Вернуть следующий доступный ключ. Ждёт если все заблокированы."""
        while True:
            async with self._lock:
                now = time.monotonic()
                for _ in range(len(self._keys)):
                    idx = self._next_idx
                    self._next_idx = (self._next_idx + 1) % len(self._keys)
                    if now >= self._blocked_until[idx]:
                        return self._keys[idx]
                min_wait = min(self._blocked_until) - now
            await asyncio.sleep(max(0.1, min_wait))

    def mark_rate_limited(self, key_index: int, block_seconds: float = 60.0) -> None:
        """Пометить ключ как получивший 429."""
        self._blocked_until[key_index] = time.monotonic() + block_seconds
        print(f"  Groq ключ {key_index + 1}/{len(self._keys)} заблокирован на {block_seconds:.0f}с")

    def key_index(self, key: str) -> int:
        """Вернуть индекс ключа в пуле."""
        return self._keys.index(key)


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


def _retry_delay(attempt: int) -> int:
    return 2 ** attempt * 5


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
    pool: GroqKeyPool,
    offset_seconds: float,
    semaphore: asyncio.Semaphore,
    max_retries: int = 5,
) -> list[dict[str, Any]]:
    async with semaphore:
        file_bytes = chunk_path.read_bytes()
        # Ретраи при таймаутах и транзиентных ошибках
        for attempt in range(max_retries):
            # Запрашиваем ключ на каждый attempt — пул выдаст незаблокированный
            api_key = await pool.acquire()
            headers = {"Authorization": f"Bearer {api_key}"}
            # httpx 0.28+ требует передавать multipart-данные единым списком;
            # пересоздаём payload на каждый ретрай, т.к. httpx потребляет его
            files_payload = [
                ("model", (None, "whisper-large-v3")),
                ("response_format", (None, "verbose_json")),
                ("timestamp_granularities[]", (None, "word")),
                ("file", (chunk_path.name, file_bytes, "audio/mpeg")),
            ]
            try:
                response = await client.post(
                    GROQ_TRANSCRIBE_URL,
                    headers=headers,
                    files=files_payload,
                )
                response.raise_for_status()
                # Парсим ответ сразу после успешного запроса — response гарантированно определён
                payload = response.json()
                break
            except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError, httpx.ReadError) as exc:
                if attempt < max_retries - 1:
                    await asyncio.sleep(_retry_delay(attempt))
                    continue
                raise
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (429, 503) and attempt < max_retries - 1:
                    # Groq rate-limit — блокируем использованный ключ, пул выберет другой
                    pool.mark_rate_limited(pool.key_index(api_key))
                    continue
                if exc.response.status_code == 524 and attempt < max_retries - 1:
                    # Таймаут на стороне апстрима: ждём и повторяем тот же чанк.
                    await asyncio.sleep(_retry_delay(attempt))
                    continue
                raise
        else:
            # Все попытки исчерпаны без успешного ответа (не должно достигаться при raise выше)
            raise RuntimeError(f"Не удалось транскрибировать чанк {chunk_path.name} за {max_retries} попыток")
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
    groq_api_keys: list[str],
    on_progress: Callable[[int], Any],
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    await _emit_progress(on_progress, 5)

    await _run_ffmpeg_segment(audio_path, output_dir)
    chunk_paths = sorted(output_dir.glob("chunk_*.mp3"))
    if not chunk_paths:
        raise RuntimeError("ffmpeg не создал сегменты аудио")

    await _emit_progress(on_progress, 20)

    pool = GroqKeyPool(groq_api_keys)
    semaphore = asyncio.Semaphore(1)
    words: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=60.0)) as client:
        tasks = [
            asyncio.create_task(
                _transcribe_chunk(
                    client=client,
                    chunk_path=chunk_path,
                    pool=pool,
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
